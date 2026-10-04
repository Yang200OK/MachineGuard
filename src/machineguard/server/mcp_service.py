"""MCP 认证、授权、预算与审计；业务结果复用现有可信服务。"""

from datetime import timedelta
from hashlib import sha256
import json
import re
import secrets
from threading import Event
import time
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import func, select, text
from .auth_service import Principal, ServiceError, audit
from . import device_service, report_service
from .mcp_models import McpCredential
from .mcp_contract import MCP_TOOLS, has_tool_permission
from .models import AuditEvent, SessionRecord, User
from .schemas import PredictionRequest
from .security import token_digest
from .tool_runtime import ToolRunner, ToolScope, fresh_principal
from .index_models import KnowledgeCatalog, KnowledgeSnapshot
from .qa_config import load_qa_configuration
from .graph_path_service import search_paths


def digest(value):
    return sha256(json.dumps(jsonable_encoder(value), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def formal_account(principal):
    if principal.user.is_guest:
        raise ServiceError(403, "MCP_FORMAL_ACCOUNT_REQUIRED", "访客不能签发或使用外部 MCP 凭据。")


def credential_view(session, row):
    parent = session.get(SessionRecord, row.session_id)
    now = session.scalar(select(func.clock_timestamp()))
    state = "revoked" if row.revoked_at else "session_ended" if parent.revoked_at else "expired" if min(row.expires_at, parent.expires_at) <= now else "active"
    return {"credential_id": str(row.id), "client_name": row.client_name, "scopes": row.scopes,
            "created_at": row.created_at, "expires_at": min(row.expires_at, parent.expires_at),
            "revoked_at": row.revoked_at, "status": state}


def issue_credential(session, principal, payload, config, request_id):
    principal = fresh_principal(session, principal); formal_account(principal)
    if any(not has_tool_permission(principal, name) for name in payload.scopes):
        raise ServiceError(403, "MCP_SCOPE_FORBIDDEN", "不能签发超出当前角色的 MCP 能力。")
    lock_key = int.from_bytes(sha256(f"mcp-issue:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    fingerprint = digest({**payload.model_dump(mode="json"), "session_id": str(principal.session.id)})
    previous = session.scalar(select(McpCredential).where(McpCredential.owner_id == principal.user.id,
        McpCredential.idempotency_key == payload.idempotency_key))
    if previous is not None:
        if previous.request_fingerprint != fingerprint:
            raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "该请求键已用于不同的 MCP 签发参数。")
        return {"credential": credential_view(session, previous), "access_token": None, "replayed": True}
    now = session.scalar(select(func.clock_timestamp()))
    active = session.scalar(select(func.count()).select_from(McpCredential).join(SessionRecord,
        McpCredential.session_id == SessionRecord.id).where(McpCredential.owner_id == principal.user.id,
        McpCredential.revoked_at.is_(None), McpCredential.expires_at > now,
        SessionRecord.revoked_at.is_(None), SessionRecord.expires_at > now))
    if active >= config.max_active_credentials:
        raise ServiceError(429, "MCP_CREDENTIAL_LIMIT", "有效 MCP 凭据数量达到上限，请先撤销不再使用的凭据。")
    expires = min(now + timedelta(minutes=payload.expires_minutes), principal.session.expires_at)
    if expires <= now + timedelta(minutes=1):
        raise ServiceError(401, "MCP_SESSION_EXPIRING", "当前登录会话即将到期，请重新登录后签发。")
    token = "mgmcp_" + secrets.token_urlsafe(32)
    row = McpCredential(owner_id=principal.user.id, session_id=principal.session.id,
        idempotency_key=payload.idempotency_key, request_fingerprint=fingerprint, token_hash=token_digest(token),
        client_name=payload.client_name, scopes=payload.scopes, expires_at=expires)
    session.add(row); session.flush()
    audit(session, principal.user.id, "mcp.credential_created", "mcp_credential", row.id, request_id,
          {"client_name": row.client_name, "scopes": row.scopes, "expires_at": expires.isoformat()})
    return {"credential": credential_view(session, row), "access_token": token, "replayed": False}


def identity(session, token):
    if not isinstance(token, str) or not re.fullmatch(r"mgmcp_[A-Za-z0-9_-]{43}", token):
        raise ServiceError(401, "MCP_CREDENTIAL_INVALID", "MCP 凭据无效、到期或已撤销。")
    session.expire_all()
    row = session.scalar(select(McpCredential).where(McpCredential.token_hash == token_digest(token)))
    now = session.scalar(select(func.clock_timestamp()))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise ServiceError(401, "MCP_CREDENTIAL_INVALID", "MCP 凭据无效、到期或已撤销。")
    user = session.get(User, row.owner_id); parent = session.get(SessionRecord, row.session_id)
    if user is None or parent is None:
        raise ServiceError(401, "MCP_CREDENTIAL_INVALID", "MCP 凭据身份依据不可用。")
    principal = fresh_principal(session, Principal(user, parent)); formal_account(principal)
    return principal, row


def list_credentials(session, principal, offset=0):
    formal_account(principal)
    query = select(McpCredential).where(McpCredential.owner_id == principal.user.id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(McpCredential.created_at.desc(), McpCredential.id.desc()).offset(offset).limit(20)).all()
    return {"items": [credential_view(session, row) for row in rows], "total": total, "offset": offset, "limit": 20}


def revoke_credential(session, principal, credential_id, request_id):
    formal_account(principal)
    row = session.scalar(select(McpCredential).where(McpCredential.id == credential_id,
        McpCredential.owner_id == principal.user.id).with_for_update())
    if row is None:
        raise ServiceError(404, "MCP_CREDENTIAL_NOT_FOUND", "MCP 凭据不存在或不属于当前账号。")
    if row.revoked_at is None:
        row.revoked_at = session.scalar(select(func.clock_timestamp()))
        audit(session, principal.user.id, "mcp.credential_revoked", "mcp_credential", row.id, request_id, {})
    return credential_view(session, row)


def activity(session, principal, offset=0):
    formal_account(principal)
    query = select(AuditEvent).where(AuditEvent.actor_id == principal.user.id,
                                   AuditEvent.action == "mcp.operation_executed")
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset(offset).limit(20)).all()
    return {"items": [{"request_id": str(row.request_id), "created_at": row.created_at, **row.details} for row in rows],
            "total": total, "offset": offset, "limit": 20}


class McpRuntime:
    def __init__(self, database, runtime, registry, config):
        self.database, self.runtime, self.registry, self.config = database, runtime, registry, config

    def verify(self, token):
        with self.database.sessions() as session:
            identity(session, token)

    def tools(self, principal, credential):
        return [name for name in MCP_TOOLS if name in credential.scopes and has_tool_permission(principal, name)]

    def execute(self, token, operation, arguments, transport, *, method=None):
        """一个受限事务内完成业务与审计；失败回滚副作用，随后保存失败记录。"""
        request_id = uuid4(); started = time.monotonic(); failure = None; result = None
        with self.database.sessions.begin() as session:
            principal, credential = identity(session, token)
            credential_id = credential.id; owner_id = principal.user.id
            event = {"credential_id": str(credential_id), "client_name": credential.client_name,
                     "operation": operation[:96], "method": method, "transport": transport}
            try:
                try:
                    event["input_sha256"] = digest(arguments)
                except (TypeError, ValueError):
                    raise ServiceError(422, "MCP_ARGUMENTS_INVALID", "MCP 参数必须是有限数值组成的 JSON 数据。") from None
                with session.begin_nested():
                    key = int.from_bytes(sha256(f"mcp-call:{credential_id}".encode()).digest()[:8], "big", signed=True)
                    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
                        raise ServiceError(429, "MCP_BUSY", "同一 MCP 凭据正在执行另一项操作，请稍后明确重试。")
                    recent = select(AuditEvent).where(AuditEvent.action == "mcp.operation_executed",
                        AuditEvent.target_id == credential_id, AuditEvent.created_at > func.clock_timestamp() - timedelta(minutes=1))
                    count = session.scalar(select(func.count()).select_from(recent.subquery()))
                    if count >= self.config.max_calls_per_minute:
                        raise ServiceError(429, "MCP_CALL_BUDGET", "本凭据达到每分钟 MCP 操作上限。")
                    if operation == "search_maintenance_knowledge":
                        cloud_count = session.scalar(select(func.count()).select_from(recent.where(
                            AuditEvent.details["operation"].astext == operation).subquery()))
                        if cloud_count >= self.config.max_knowledge_calls_per_minute:
                            raise ServiceError(429, "MCP_KNOWLEDGE_BUDGET", "本凭据达到每分钟知识云检索上限。")
                    result = self.dispatch(session, principal, credential, operation, arguments, request_id)
                    checked, fresh = identity(session, token)
                    if operation in MCP_TOOLS and (operation not in fresh.scopes or not has_tool_permission(checked, operation)):
                        raise ServiceError(403, "MCP_SCOPE_FORBIDDEN", "操作期间 MCP 能力或账号权限发生变化。")
                    if time.monotonic() - started > 120:
                        raise ServiceError(429, "MCP_TIME_BUDGET", "本次 MCP 操作超过时间预算，结果未提交。")
                    result = jsonable_encoder(result)
                    event.update(status="succeeded", result_sha256=digest(result))
            except ServiceError as caught:
                failure = caught; event.update(status="failed", error_code=caught.code)
            except ValidationError:
                failure = ServiceError(422, "MCP_ARGUMENTS_INVALID", "MCP 参数类型、字段或限额不符合约定。")
                event.update(status="failed", error_code=failure.code)
            except Exception as caught:
                failure = ServiceError(500, "MCP_OPERATION_FAILED", "MCP 操作失败，请依据请求编号检查服务日志。")
                event.update(status="failed", error_code=failure.code, error_type=type(caught).__name__)
            event["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            audit(session, owner_id, "mcp.operation_executed", "mcp_credential", credential_id, request_id, event)
        if failure is not None:
            return {"request_id": str(request_id), "error": {"code": failure.code, "message": failure.message, "status": failure.status}}
        return {"request_id": str(request_id), "data": result}

    def dispatch(self, session, principal, credential, operation, arguments, request_id):
        allowed = self.tools(principal, credential)
        if operation == "capabilities":
            if arguments:
                raise ServiceError(422, "MCP_ARGUMENTS_INVALID", "能力发现不接受额外参数。")
            return {"tools": [{"name": name, "description": MCP_TOOLS[name].description,
                      "inputSchema": MCP_TOOLS[name].arguments.model_json_schema(), "writes_prediction": MCP_TOOLS[name].writes}
                     for name in allowed], "reports_available": "read_report" in allowed,
                    "limitation": "MCP 不扩大角色权限；预测会幂等保存本人记录，资料检索返回候选证据，路径不证明因果或故障。"}
        if operation not in allowed:
            raise ServiceError(403, "MCP_TOOL_FORBIDDEN", "该工具未获凭据授权或不在 MCP 代码白名单。")
        payload = MCP_TOOLS[operation].arguments.model_validate(arguments)
        if operation == "list_engines":
            return device_service.list_engines(session, principal, payload.offset, payload.limit)
        if operation == "read_report":
            return report_service.report_view(report_service.authorized_report(session, principal, payload.report_id), session)
        if operation == "search_evidence_paths":
            return search_paths(session, principal, payload)
        if operation == "search_maintenance_knowledge":
            # 原工具服务负责混合检索、重排、固定快照与逐条来源检查，不复制 RAG 算法。
            catalog = session.get(KnowledgeCatalog, 1)
            selected = catalog.current_id if catalog is not None else None
            snapshot = session.get(KnowledgeSnapshot, selected) if selected else None
            if snapshot is None or snapshot.published_at is None:
                raise ServiceError(409, "KNOWLEDGE_NOT_PUBLISHED", "当前没有完整已发布知识快照。")
            scope = ToolScope(uuid4(), principal.user.id, principal.session.id, None, None, None, None,
                snapshot.id, snapshot.manifest_sha256, load_qa_configuration().model_dump_json(by_alias=True))
            runner = ToolRunner.from_frozen(self.database, self.runtime, self.registry.load("maintenance-qa"),
                scope, (operation,), None, Event(), None)
            return runner._knowledge(session, principal, payload)
        engine, dataset = device_service.authorized_engine(session, principal, payload.engine_key)
        device_service.bounded_frame(session, engine, payload.end_cycle, payload.end_cycle)
        if operation == "get_engine_overview":
            view = device_service.engine_view(engine, dataset); view["last_observed_cycle"] = payload.end_cycle
            frame = device_service.bounded_frame(session, engine, payload.end_cycle, payload.end_cycle)
            return {"engine": view, "end_cycle": payload.end_cycle, "observation": frame.to_dict(orient="records")[0]}
        if operation == "get_sensor_trend":
            if payload.start_cycle > payload.end_cycle or payload.end_cycle - payload.start_cycle + 1 > 1000:
                raise ServiceError(422, "TOOL_RANGE_INVALID", "趋势范围必须在固定周期内且最多一千条观测。")
            result = device_service.sensor_trends(session, principal, payload.engine_key, payload.end_cycle, payload.start_cycle, payload.columns)
            result["engine"]["last_observed_cycle"] = payload.end_cycle
            return result
        if operation == "get_prediction_history":
            return device_service.prediction_history(session, principal, payload.engine_key, payload.end_cycle, payload.offset, payload.limit)
        if operation == "predict_rul":
            result, replayed = self.runtime.predict(session, principal, PredictionRequest(engine_key=payload.engine_key,
                end_cycle=payload.end_cycle, model_version=self.runtime.check_registration(session).version,
                idempotency_key="mcp-" + payload.idempotency_key.hex), request_id)
            return {**result, "replayed": replayed}
        raise ServiceError(403, "MCP_TOOL_FORBIDDEN", "此 MCP 工具没有受控实现。")
