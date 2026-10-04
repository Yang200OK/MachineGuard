"""服务器固定范围的工具运行器；Skill 和模型正文不参与授权。"""

from dataclasses import dataclass
from hashlib import sha256
import json
from threading import Event, Lock
import time
from types import MappingProxyType, SimpleNamespace
from uuid import UUID, uuid4

from pydantic import Field, ValidationError, field_validator
from sqlalchemy import func, select

from .auth_service import Principal, ServiceError, audit
from . import device_service
from .business_models import PredictionRecord
from .index_models import KnowledgeCatalog, KnowledgeSnapshot, SnapshotChunk
from .index_service import member_valid, search_preview
from .models import SessionRecord, User
from .parse_models import DocumentChunk
from .qa_config import load_qa_configuration, QAConfig
from .qa_provider import rerank
from .schemas import PredictionRequest, StrictRequest
from .security import permissions
from .graph_path_service import PathArguments, search_paths


class EmptyArguments(StrictRequest):
    pass


class TrendArguments(StrictRequest):
    start_cycle: int = Field(ge=1)
    columns: list[str] = Field(min_length=1, max_length=6)

    @field_validator("columns")
    @classmethod
    def valid_columns(cls, values):
        if len(set(values)) != len(values) or any(x not in device_service.RAW_COLUMNS[2:] for x in values):
            raise ValueError("字段必须唯一且属于原始设置或传感器。")
        return values


class HistoryArguments(StrictRequest):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=20)


class KnowledgeArguments(StrictRequest):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        if not value.strip() or any(ord(x) < 32 and x not in "\n\r\t" for x in value):
            raise ValueError("查询不能为空或包含非法控制字符。")
        return value.strip()


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments: type[StrictRequest]
    permission: str
    writes_prediction: bool = False


TOOLS = MappingProxyType({item.name: item for item in (
    ToolDefinition("get_engine_overview", "读取固定设备在选定周期可见的概况。", EmptyArguments, "devices:read"),
    ToolDefinition("get_sensor_trend", "计算有界区间的真实传感器观察和斜率。", TrendArguments, "devices:read"),
    ToolDefinition("predict_rul", "使用已登记 LSTM 预测并幂等保存本人可信记录。", EmptyArguments, "predictions:create", True),
    ToolDefinition("get_prediction_history", "读取本人在固定周期以内的预测历史。", HistoryArguments, "devices:read"),
    ToolDefinition("search_maintenance_knowledge", "固定快照内混合检索并重排，只返回达到证据门槛的来源。", KnowledgeArguments, "knowledge:manage"),
    ToolDefinition("search_evidence_paths", "精确实体名查询最多两跳有向已审核关系；同名需澄清，每条边返回固定原文。", PathArguments, "knowledge:manage"),
)})
# 依赖状态由代码指定，Skill 文本不能把未实现的服务标为可用。
SKILL_TOOLS = MappingProxyType({
    "engine-analysis": ("get_engine_overview", "get_sensor_trend", "predict_rul", "get_prediction_history", "search_maintenance_knowledge"),
    "sensor-trend-analysis": ("get_engine_overview", "get_sensor_trend"),
    "maintenance-qa": ("search_maintenance_knowledge",),
    "report-generation": (), "evidence-path-analysis": ("search_evidence_paths",),
})
UNAVAILABLE = MappingProxyType({"report-generation": "固定一键候选可在报告与审核页面人工批准并保存正式报告；不支持自由问题自动审批。"})
KNOWLEDGE_SKILLS = frozenset({"maintenance-qa", "evidence-path-analysis"})


def fresh_principal(session, principal):
    session.expire_all()
    user = session.get(User, principal.user.id)
    record = session.get(SessionRecord, principal.session.id)
    now = session.scalar(select(func.clock_timestamp()))
    if (user is None or record is None or record.user_id != user.id or not user.is_active
            or record.revoked_at is not None or record.expires_at <= now):
        raise ServiceError(401, "SESSION_INVALID", "工具调用会话已过期、撤销或账号停用。")
    return Principal(user, record)


def permitted_tools(principal, name):
    required = "knowledge:manage" if name in KNOWLEDGE_SKILLS else "analysis:create"
    principal.require(required)
    result = []
    for tool in SKILL_TOOLS[name]:
        try:
            principal.require(TOOLS[tool].permission)
        except ServiceError:
            continue
        result.append(tool)
    return tuple(result)


def skill_summary(registry, principal, name, version=None):
    item = registry.describe(name, version)
    reason = UNAVAILABLE.get(name)
    if name == "report-generation" and item.version == "1.0.0":
        reason = "此版本没有报告候选流程；1.1.0 支持固定一键候选，可到报告与审核页面人工审核。"
    if name == "evidence-path-analysis" and item.version == "1.0.0":
        reason = "此历史版本没有路径执行流程；1.1.0 支持最多两跳的已审核路径。"
    try:
        tools = permitted_tools(principal, name)
    except ServiceError:
        tools = (); reason = reason or "当前身份没有选择此 Skill 的权限。"
    if name == "evidence-path-analysis" and item.version == "1.0.0":
        tools = ()
    return {"name": item.name, "description": item.description, "version": item.version, "sha256": item.sha256,
            "status": "unavailable" if reason else "available", "reason": reason,
            "candidate_available": name == "report-generation" and item.version != "1.0.0" and "analysis:create" in permissions([role.name for role in principal.user.roles]),
            "tools": list(tools), "versions": [version for skill, version in registry.entries if skill == name]}


def tool_schemas(names):
    return [{"type": "function", "function": {"name": name, "description": TOOLS[name].description,
             "parameters": TOOLS[name].arguments.model_json_schema()}, "writes_prediction": TOOLS[name].writes_prediction}
            for name in names]


@dataclass(frozen=True)
class ToolScope:
    run_id: UUID
    owner_id: UUID
    session_id: UUID
    engine_key: str | None
    end_cycle: int | None
    data_version: str | None
    model_version: str | None
    snapshot_id: UUID | None
    snapshot_manifest: str | None
    qa_config_json: str


def validate_knowledge_items(session, scope, items):
    """候选必须同时匹配固定快照、审核来源和原解析位置。"""
    snapshot = session.get(KnowledgeSnapshot, scope.snapshot_id)
    if snapshot is None or snapshot.published_at is None or snapshot.manifest_sha256 != scope.snapshot_manifest:
        raise ServiceError(409, "TOOL_KNOWLEDGE_CHANGED", "固定知识快照不再符合已加载身份。")
    for item in items:
        member = next((m for m in snapshot.members if m["version_id"] == item["version_id"]), None)
        indexed = session.get(SnapshotChunk, (scope.snapshot_id, UUID(item["chunk_id"])))
        original = session.get(DocumentChunk, UUID(item["chunk_id"]))
        if member is None or not member_valid(session, member) or indexed is None or original is None:
            raise ServiceError(409, "TOOL_SOURCE_CHANGED", "检索期间来源或审核依据发生变化。")
        expected_location = {"source_id": str(original.source_id), "parse_id": str(original.parse_id),
            "document_version": member["version_number"], "page": original.page, "page_spans": original.page_spans,
            "section": original.section, "language": original.language,
            "char_start": original.char_start, "char_end": original.char_end}
        if (str(original.document_version) != member["version_id"] or str(original.parse_id) != member["parse_id"]
                or indexed.text != original.text or indexed.text != item["text"]
                or indexed.sha256 != original.sha256 or original.sha256 != sha256(original.text.encode("utf-8")).hexdigest()
                or indexed.source_metadata != member["source_metadata"] or item["source_metadata"] != indexed.source_metadata
                or indexed.location != expected_location or any(item.get(k) != v for k, v in expected_location.items())):
            raise ServiceError(409, "TOOL_SOURCE_LOCATION_INVALID", "候选正文、版本或页面位置与原解析记录不一致。")


class ToolRunner:
    def __init__(self, database, runtime, registry, principal, skill_name, *, engine_key=None, end_cycle=None, snapshot_id=None):
        self.database = database; self.runtime = runtime
        self.skill = registry.load(skill_name)
        if skill_name in UNAVAILABLE or (skill_name == "evidence-path-analysis" and self.skill.version == "1.0.0"):
            raise ServiceError(409, "SKILL_DEPENDENCY_UNAVAILABLE", UNAVAILABLE.get(skill_name, "历史路径 Skill 未开放执行。"))
        self.lock = Lock(); self.cancelled = Event(); self.started = time.monotonic()
        self.calls = 0; self._trace = []
        self.execution_guard = None
        self.cloud_client = None
        with database.sessions() as session:
            principal = fresh_principal(session, principal)
            self.allowed = permitted_tools(principal, skill_name)
            data_version = None; model_version = None
            if skill_name not in KNOWLEDGE_SKILLS:
                if not isinstance(engine_key, str) or type(end_cycle) is not int:
                    raise ServiceError(422, "TOOL_SCOPE_REQUIRED", "设备分析必须固定设备和末端观测周期。")
                engine, dataset = device_service.authorized_engine(session, principal, engine_key)
                device_service.bounded_frame(session, engine, end_cycle, end_cycle)
                data_version = dataset.source_sha256
                model_version = runtime.check_registration(session).version
            elif engine_key is not None or end_cycle is not None:
                raise ServiceError(422, "TOOL_SCOPE_INVALID", "资料问答 Skill 不接受设备预测范围。")
            snapshot = None
            if any(tool in self.allowed for tool in ("search_maintenance_knowledge", "search_evidence_paths")):
                if snapshot_id is not None and not isinstance(snapshot_id, UUID):
                    raise ServiceError(422, "TOOL_SCOPE_INVALID", "知识快照身份必须由服务器校验为 UUID。")
                selected = snapshot_id or session.get(KnowledgeCatalog, 1).current_id
                snapshot = session.get(KnowledgeSnapshot, selected) if selected else None
                if snapshot is None or snapshot.published_at is None:
                    raise ServiceError(409, "KNOWLEDGE_NOT_PUBLISHED", "尚无完整已发布知识快照。")
            elif snapshot_id is not None:
                raise ServiceError(403, "TOOL_SCOPE_FORBIDDEN", "当前 Skill 或身份不能绑定知识快照。")
            self.scope = ToolScope(uuid4(), principal.user.id, principal.session.id, engine_key, end_cycle,
                data_version, model_version, snapshot.id if snapshot else None,
                snapshot.manifest_sha256 if snapshot else None, load_qa_configuration().model_dump_json(by_alias=True))

    @property
    def trace(self):
        return [json.loads(item) for item in self._trace]

    @classmethod
    def from_frozen(cls, database, runtime, skill, scope, allowed, guard, interrupted, cloud_client):
        """Worker 只接受服务器持久化的身份和版本，模型不能重新绑定范围。"""
        runner = cls.__new__(cls)
        runner.database, runner.runtime, runner.skill, runner.scope = database, runtime, skill, scope
        runner.allowed = tuple(allowed)
        runner.lock = Lock(); runner.cancelled = interrupted; runner.started = time.monotonic()
        runner.calls = 0; runner._trace = []; runner.execution_guard = guard
        runner.cloud_client = cloud_client
        return runner

    def _principal(self, session):
        # 每次根据服务器范围重建身份，不接受模型提交 owner_id 或 session_id。
        user = session.get(User, self.scope.owner_id)
        record = session.get(SessionRecord, self.scope.session_id)
        if user is None or record is None:
            raise ServiceError(401, "SESSION_INVALID", "运行身份依据不可用。")
        principal = fresh_principal(session, Principal(user, record))
        permitted_tools(principal, self.skill.name)
        return principal

    def _check(self, session):
        if self.cancelled.is_set():
            raise ServiceError(409, "TOOL_RUN_CANCELLED", "工具运行已取消。")
        if time.monotonic() - self.started > 360:
            raise ServiceError(429, "TOOL_TIME_BUDGET", "工具运行超过本轮活跃时间限额。")
        principal = self._principal(session)
        if self.scope.engine_key is not None:
            engine, dataset = device_service.authorized_engine(session, principal, self.scope.engine_key)
            if dataset.source_sha256 != self.scope.data_version or engine.last_cycle < self.scope.end_cycle:
                raise ServiceError(409, "TOOL_DATA_CHANGED", "设备数据版本或固定周期依据已变化。")
            if self.runtime.check_registration(session).version != self.scope.model_version:
                raise ServiceError(409, "TOOL_MODEL_CHANGED", "固定模型版本与当前登记不一致。")
        return principal

    def execute(self, name, arguments, request_id, *, completion=None):
        if not isinstance(request_id, UUID):
            raise ServiceError(422, "TOOL_REQUEST_INVALID", "工具调用必须带服务器请求编号。")
        with self.lock:
            if self.calls >= 8:
                raise ServiceError(429, "TOOL_CALL_BUDGET", "已达到八次工具调用上限。")
            self.calls += 1
            event = {"run_id": str(self.scope.run_id), "sequence": self.calls, "selected_skill": self.skill.name,
                     "skill_version": self.skill.version, "skill_sha256": self.skill.sha256,
                     "tool_allowlist": list(self.allowed), "tool": str(name)[:96], "request_id": str(request_id)}
            error = None; result = None
            with self.database.sessions.begin() as session:
                try:
                    with session.begin_nested():
                        principal = self._check(session)
                        if self.execution_guard is not None:
                            self.execution_guard(session, False)
                        if not isinstance(name, str) or name not in self.allowed:
                            raise ServiceError(403, "TOOL_NOT_ALLOWED", "此工具不在当前 Skill 的代码白名单内。")
                        principal.require(TOOLS[name].permission)
                        try:
                            payload = TOOLS[name].arguments.model_validate(arguments)
                        except ValidationError:
                            raise ServiceError(422, "TOOL_ARGUMENTS_INVALID", "工具参数类型、字段或限额不符合约定。") from None
                        canonical = payload.model_dump_json()
                        event["input_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
                        result = self._dispatch(session, principal, name, payload, request_id)
                        # 云请求后仍要重新核对权限、取消与时间，失败回滚本次副作用。
                        checked = self._check(session)
                        checked.require(TOOLS[name].permission)
                        if self.execution_guard is not None:
                            self.execution_guard(session, True)
                        event["result_sha256"] = sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                            allow_nan=False).encode("utf-8")).hexdigest()
                        event["status"] = "succeeded"
                        if completion is not None:
                            # 恢复流程的工具结果与业务副作用同一事务保存，关闭提交间隙。
                            completion(session, self.observation(name, result), event)
                except ServiceError as caught:
                    error = caught; event.update(status="failed", error_code=caught.code)
                except Exception as caught:
                    error = ServiceError(500, "TOOL_EXECUTION_FAILED", "工具执行失败；未切换工具或重复调用。")
                    event.update(status="failed", error_code=error.code, error_type=type(caught).__name__)
                audit(session, self.scope.owner_id, "agent.tool_executed", "agent_scope", self.scope.run_id, request_id, event)
            self._trace.append(json.dumps(event, ensure_ascii=False, allow_nan=False))
            if error is not None:
                raise error
            return self.observation(name, result)

    def observation(self, name, result):
        return {"scope": {"engine_key": self.scope.engine_key, "end_cycle": self.scope.end_cycle,
                "data_version": self.scope.data_version, "model_version": self.scope.model_version,
                "snapshot_id": str(self.scope.snapshot_id) if self.scope.snapshot_id else None},
                "tool": name, "result": result, "validation": "passed"}

    def _dispatch(self, session, principal, name, payload, request_id):
        scope = self.scope
        if name == "get_engine_overview":
            engine, dataset = device_service.authorized_engine(session, principal, scope.engine_key)
            observation = device_service.bounded_frame(session, engine, scope.end_cycle, scope.end_cycle)
            view = device_service.engine_view(engine, dataset)
            # 已导入的末端周期不作为早期分析的当前设备状态。
            view["last_observed_cycle"] = scope.end_cycle
            return {"engine": view, "end_cycle": scope.end_cycle, "observation": observation.to_dict(orient="records")[0]}
        if name == "get_sensor_trend":
            if not 1 <= payload.start_cycle <= scope.end_cycle or scope.end_cycle - payload.start_cycle + 1 > 1000:
                raise ServiceError(422, "TOOL_RANGE_INVALID", "趋势区间必须在固定周期以内且最多一千条观测。")
            result = device_service.sensor_trends(session, principal, scope.engine_key, scope.end_cycle, payload.start_cycle, payload.columns)
            result["engine"]["last_observed_cycle"] = scope.end_cycle
            return result
        if name == "get_prediction_history":
            return device_service.prediction_history(session, principal, scope.engine_key, scope.end_cycle, payload.offset, payload.limit)
        if name == "predict_rul":
            self.runtime.check_registration(session)
            result, replayed = self.runtime.predict(session, principal, PredictionRequest(engine_key=scope.engine_key,
                end_cycle=scope.end_cycle, model_version=scope.model_version,
                idempotency_key="agent-" + scope.run_id.hex), request_id)
            row = session.get(PredictionRecord, UUID(result["prediction_id"]))
            if (row.owner_id != scope.owner_id or row.engine_key != scope.engine_key
                    or row.end_cycle != scope.end_cycle or row.model_version != scope.model_version
                    or result != device_service.prediction_view(row)):
                raise ServiceError(409, "TOOL_PREDICTION_INVALID", "预测输出与本人固定范围的可信记录不一致。")
            return {**result, "replayed": replayed}
        if name == "search_maintenance_knowledge":
            return self._knowledge(session, principal, payload)
        if name == "search_evidence_paths":
            return search_paths(session, principal, payload, snapshot_id=scope.snapshot_id, manifest=scope.snapshot_manifest)
        raise ServiceError(403, "TOOL_NOT_ALLOWED", "未注册此工具实现。")

    def _knowledge(self, session, principal, payload):
        scope = self.scope
        snapshot = session.get(KnowledgeSnapshot, scope.snapshot_id)
        if snapshot is None or snapshot.published_at is None or snapshot.manifest_sha256 != scope.snapshot_manifest:
            raise ServiceError(409, "TOOL_KNOWLEDGE_CHANGED", "固定知识快照不再符合已加载身份。")
        config = QAConfig.model_validate_json(scope.qa_config_json)
        recalled = search_preview(session, principal, SimpleNamespace(query=payload.query,
            snapshot_id=scope.snapshot_id, equipment_type=None, limit=config.recall_limit), client=self.cloud_client)
        items = recalled["items"]; usage = []
        if recalled["query_usage"] is not None:
            usage.append({"stage": "embedding", **recalled["query_usage"]})
        if not items:
            return {"status": "no_evidence", "snapshot_id": str(scope.snapshot_id), "evidence": [], "usage": usage,
                    "reason": recalled.get("reason", "没有有效知识来源。")}
        validate_knowledge_items(session, scope, items)
        ranked, receipt = rerank(config, payload.query, [item["text"] for item in items], client=self.cloud_client); usage.append(receipt)
        principal = self._check(session)
        principal.require("knowledge:manage")
        snapshot = session.get(KnowledgeSnapshot, scope.snapshot_id)
        if snapshot is None or snapshot.manifest_sha256 != scope.snapshot_manifest:
            raise ServiceError(409, "TOOL_KNOWLEDGE_CHANGED", "知识请求期间固定快照发生变化。")
        validate_knowledge_items(session, scope, items)
        evidence = [{**items[item["index"]], "rerank_score": item["relevance_score"], "evidence_id": f"E{index}"}
            for index, item in enumerate([x for x in ranked if x["relevance_score"] >= config.min_rerank_score][:payload.limit], 1)]
        return {"status": "evidence" if evidence else "no_evidence", "snapshot_id": str(scope.snapshot_id),
                "evidence": evidence, "usage": usage, "min_rerank_score": config.min_rerank_score,
                "limitation": "返回候选证据；分数不是概率，尚未生成或核对结论。"}
