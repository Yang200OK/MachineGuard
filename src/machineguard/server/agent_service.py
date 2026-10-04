"""固定分析身份、预算和事件；终态与可信结果同一事务保存。"""

from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4
from sqlalchemy import func, select, text
from .agent_config import load_agent_configuration
from .agent_models import AgentRun, AgentEvent
from .auth_service import Principal, ServiceError, audit
from . import device_service, job_service
from .business_models import PredictionRecord
from .index_models import KnowledgeCatalog, KnowledgeSnapshot
from .job_models import Job
from .models import User, SessionRecord
from .qa_config import load_qa_configuration
from .tool_runtime import fresh_principal, skill_summary, ToolScope, validate_knowledge_items


def jsonable(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False, allow_nan=False))


def add_event(session, row, kind, details):
    row.event_sequence += 1
    session.add(AgentEvent(run_id=row.id, sequence=row.event_sequence, kind=kind, details=jsonable(details)))


def principal_for(session, row):
    from .report_models import ReportReview
    decision = session.get(ReportReview, row.id)
    if decision is not None:
        from .report_service import execution_principal
        return execution_principal(session, row, decision)
    user = session.get(User, row.owner_id); record = session.get(SessionRecord, row.session_id)
    if user is None or record is None:
        raise ServiceError(401, "SESSION_INVALID", "分析提交身份已失效。")
    principal = fresh_principal(session, Principal(user, record)); principal.require("analysis:create")
    return principal


def owned_run(session, principal, run_id, include_deleted=False):
    principal.require("analysis:create")
    query = select(AgentRun).join(Job, AgentRun.job_id == Job.id).where(
        AgentRun.id == run_id, AgentRun.owner_id == principal.user.id)
    if not include_deleted:
        query = query.where(Job.deleted_at.is_(None))
    row = session.scalar(query)
    if row is None:
        raise ServiceError(404, "AGENT_NOT_FOUND", "分析不存在或不属于当前身份。")
    return row


def view(session, row):
    from .report_service import review_summary
    job = session.get(Job, row.job_id)
    return {"run_id": str(row.id), "workflow": row.workflow, "workflow_options": row.workflow_options, "question": row.question, "scope": row.input_scope,
        "selected_skill": row.selected_skill, "model_calls": row.model_calls, "tool_calls": row.tool_calls,
        "event_sequence": row.event_sequence, "result": row.result if job.status in ("succeeded", "waiting_review") else None,
        "recovery": {"version": row.recovery_version, "thread_id": str(row.id), "checkpoint_id": row.checkpoint_id} if row.recovery_version else None,
        "review": review_summary(session, row),
        "job": job_service.job_view(session, job)}


def list_runs(session, principal, offset, limit, deleted=False):
    principal.require("analysis:create")
    query = select(AgentRun).join(Job, AgentRun.job_id == Job.id).where(
        AgentRun.owner_id == principal.user.id)
    query = query.where(Job.deleted_at.is_not(None) if deleted else Job.deleted_at.is_(None))
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [view(session, row) for row in rows], "total": total, "offset": offset, "limit": limit}



def record_lifecycle(session, principal, run_id, restore, request_id):
    """沿用任务记录的软删除与审计，仅允许操作本人的分析。"""
    from .workspace_service import job_lifecycle
    row = owned_run(session, principal, run_id, include_deleted=True)
    # 共用任务行锁与终态限制；后台任务和分析回收站显示同一删除状态。
    result = job_lifecycle(session, principal, [row.job_id], restore, request_id)
    if not result["items"][0]["changed"]:
        raise ServiceError(409, "AGENT_PENDING", "本分析仍在执行，请先停止并等待取消完成后删除。")
    return view(session, row)


def events(session, row, after):
    rows = session.scalars(select(AgentEvent).where(AgentEvent.run_id == row.id, AgentEvent.sequence > after)
                          .order_by(AgentEvent.sequence).limit(100)).all()
    return {"items": [{"sequence": x.sequence, "kind": x.kind, "details": x.details, "created_at": x.created_at}
                      for x in rows], "last_sequence": rows[-1].sequence if rows else after,
            "has_more": bool(rows and rows[-1].sequence < row.event_sequence)}


def submit(session, principal, request, runtime, registry, request_id):
    principal = fresh_principal(session, principal); principal.require("analysis:create")
    raw = {"question": request.question, "engine_key": request.engine_key, "end_cycle": request.end_cycle}
    if request.workflow == "one_click":
        raw.update(workflow=request.workflow, workflow_options=request.workflow_options.model_dump())
    # 普通分析保持原请求指纹，旧请求确认不因新增流程字段而冲突。
    fingerprint = sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    key = int.from_bytes(sha256(f"jobs:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    previous = session.scalar(select(Job).where(Job.owner_id == principal.user.id,
                                               Job.idempotency_key == str(request.idempotency_key)))
    if previous is not None:
        if previous.kind != "agent_run" or previous.request_fingerprint != fingerprint:
            raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "此请求键已用于不同任务或分析范围。")
        row = session.scalar(select(AgentRun).where(AgentRun.job_id == previous.id))
        if previous.deleted_at is not None:
            raise ServiceError(409, "AGENT_DELETED", "原分析已移入回收站，请先恢复任务记录。")
        return {**view(session, row), "replayed": True}
    if request.workflow == "one_click":
        principal.require("devices:read"); principal.require("predictions:create")
        if request.workflow_options.include_knowledge:
            principal.require("knowledge:manage")
        engine, _ = device_service.authorized_engine(session, principal, request.engine_key)
        start = request.workflow_options.start_cycle
        if not 1 <= start <= request.end_cycle or request.end_cycle - start + 1 > 1000:
            raise ServiceError(422, "ONE_CLICK_RANGE_INVALID", "一键分析趋势范围最多一千周期，且不能超出末端周期。")
        size = runtime.predictor.preprocessing["configuration"]["window_size"]
        if request.end_cycle < size:
            raise ServiceError(422, "ONE_CLICK_WINDOW_SHORT", f"一键分析需要至少 {size} 个周期的预测窗口。")
        device_service.bounded_frame(session, engine, request.end_cycle, min(start, request.end_cycle - size + 1))
    config = load_agent_configuration()
    pending = session.scalar(select(func.count()).select_from(Job).where(Job.owner_id == principal.user.id,
                             Job.status.in_(["queued", "running", "waiting_review"])))
    if pending >= config.max_pending_per_owner:
        raise ServiceError(429, "AGENT_PENDING_LIMIT", "待处理任务已达到上限，请等待或取消。")
    if principal.user.is_guest:
        count = session.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.session_id == principal.session.id))
        if count >= config.max_guest_runs:
            raise ServiceError(429, "AGENT_GUEST_LIMIT", "本次访客会话的分析次数已达到上限。")
    scope = {"engine_key": request.engine_key, "end_cycle": request.end_cycle,
             "data_version": None, "model_version": None, "snapshot_id": None, "snapshot_manifest": None}
    if request.engine_key is not None:
        engine, dataset = device_service.authorized_engine(session, principal, request.engine_key)
        scope["data_version"] = dataset.source_sha256
        if request.end_cycle is not None:
            device_service.bounded_frame(session, engine, request.end_cycle, request.end_cycle)
        scope["model_version"] = runtime.check_registration(session).version
    catalog = [skill_summary(registry, principal, name) for name in sorted(registry.active)]
    if (request.workflow != "one_click" or request.workflow_options.include_knowledge) and any(
            tool in x["tools"] for x in catalog for tool in ("search_maintenance_knowledge", "search_evidence_paths")):
        catalog_row = session.get(KnowledgeCatalog, 1)
        snapshot = session.get(KnowledgeSnapshot, catalog_row.current_id) if catalog_row and catalog_row.current_id else None
        if snapshot is not None and snapshot.published_at is not None:
            scope.update(snapshot_id=str(snapshot.id), snapshot_manifest=snapshot.manifest_sha256)
    if request.workflow == "one_click" and request.workflow_options.include_knowledge and scope["snapshot_id"] is None:
        raise ServiceError(409, "KNOWLEDGE_NOT_PUBLISHED", "一键分析所选资料核对需要已发布的知识快照。")
    run_id = uuid4(); now = job_service.db_now(session)
    job = Job(owner_id=principal.user.id, kind="agent_run", payload={"run_id": str(run_id)},
              idempotency_key=str(request.idempotency_key), request_fingerprint=fingerprint, request_id=request_id,
              created_at=now, deadline_at=now + timedelta(seconds=config.timeout_seconds), max_attempts=3 if request.workflow == "one_click" else 1)
    session.add(job); session.flush()
    row = AgentRun(id=run_id, job_id=job.id, owner_id=principal.user.id, session_id=principal.session.id,
        question=request.question, input_scope=scope, workflow=request.workflow,
        workflow_options=request.workflow_options.model_dump() if request.workflow_options else None, config_snapshot={"agent": config.model_dump(by_alias=True),
        "qa": load_qa_configuration().model_dump(by_alias=True)}, skill_catalog=catalog,
        model_calls=0, tool_calls=0, event_sequence=0)
    if request.workflow == "one_click":
        row.recovery_version = "one-click-v1"
    session.add(row); session.flush()
    add_event(session, row, "submitted", {"scope": scope, "model": config.answer_model,
        "max_model_calls": config.max_model_calls, "max_tool_calls": config.max_tool_calls})
    audit(session, principal.user.id, "agent.submitted", "agent_run", run_id, request_id, {"job_id": str(job.id)})
    return {**view(session, row), "replayed": False}


def restore_scope(row):
    scope = row.tool_scope
    return ToolScope(row.id, row.owner_id, row.session_id, scope["engine_key"], scope["end_cycle"],
        scope["data_version"], scope["model_version"], UUID(scope["snapshot_id"]) if scope["snapshot_id"] else None,
        scope["snapshot_manifest"], json.dumps(row.config_snapshot["qa"], ensure_ascii=False))


def check_result(session, row, result):
    principal_for(session, row)
    if row.tool_scope is None:
        if result["status"] not in ("clarification", "refused") or result["observations"]:
            raise ServiceError(409, "AGENT_RESULT_INVALID", "未授权工具范围不能装配分析结果。")
        return
    scope = restore_scope(row)
    if result.get("evidence_paths") is not None and not any(x["tool"] == "search_evidence_paths" for x in result["observations"]):
        raise ServiceError(409, "AGENT_PATH_INVALID", "路径结果缺少受控工具依据。")
    if scope.engine_key is not None:
        principal = principal_for(session, row)
        engine, dataset = device_service.authorized_engine(session, principal, scope.engine_key)
        if dataset.source_sha256 != scope.data_version or engine.last_cycle < scope.end_cycle:
            raise ServiceError(409, "TOOL_DATA_CHANGED", "结果提交时固定设备依据已经变化。")
    for observation in result["observations"]:
        if observation["tool"] == "predict_rul":
            value = observation["result"]
            prediction = session.get(PredictionRecord, UUID(value["prediction_id"]))
            if (prediction is None or prediction.owner_id != row.owner_id or prediction.engine_key != scope.engine_key
                    or prediction.end_cycle != scope.end_cycle or prediction.model_version != scope.model_version
                    or {k: v for k, v in value.items() if k != "replayed"} != device_service.prediction_view(prediction)):
                raise ServiceError(409, "AGENT_PREDICTION_INVALID", "分析预测与固定业务记录不一致。")
        if observation["tool"] == "search_maintenance_knowledge":
            # 阻止审核撤销与终态提交竞态；按固定顺序锁定引用版本及解析依据。
            from .document_models import DocumentVersion
            from .parse_models import DocumentParse
            items = observation["result"]["evidence"]
            for version in sorted({x["version_id"] for x in items}):
                session.scalar(select(DocumentVersion).where(DocumentVersion.id == UUID(version)).with_for_update())
            for parsed in sorted({x["parse_id"] for x in items}):
                session.scalar(select(DocumentParse).where(DocumentParse.id == UUID(parsed)).with_for_update())
            validate_knowledge_items(session, scope, items)
            knowledge = result["knowledge"]
            if knowledge is None:
                raise ServiceError(409, "AGENT_RESULT_INVALID", "知识工具结果缺少引用核对结果。")
            from .qa_contract import validate_answer, validate_support
            draft = {"status": knowledge["status"], "reason": knowledge["reason"], "claims": [
                {"text": claim["text"], "citations": [{"evidence_id": ref["evidence_id"], "quote": ref["quote"]}
                 for ref in claim["citations"]]} for claim in knowledge["claims"]]}
            checked = validate_answer(draft, items)
            if checked["claims"] != knowledge["claims"]:
                raise ServiceError(409, "AGENT_CITATION_INVALID", "提交引用与代码计算的固定位置不一致。")
            if knowledge["status"] == "answered":
                validate_support(knowledge["support_check"], len(knowledge["claims"]))
        if observation["tool"] == "search_evidence_paths":
            from .graph_path_service import validate_result
            if (row.selected_skill["name"] != "evidence-path-analysis" or len(result["observations"]) != 1
                    or result.get("evidence_paths") != observation["result"] or result.get("knowledge") is not None):
                raise ServiceError(409, "AGENT_PATH_INVALID", "路径分析结果与受控工具记录不一致。")
            paths = observation["result"]
            if result["status"] != {"paths": "completed", "no_path": "refused", "clarification": "clarification"}.get(paths.get("status")):
                raise ServiceError(409, "AGENT_PATH_INVALID", "路径分析状态与工具结果不一致。")
            validate_result(session, principal_for(session, row), scope, paths)


def finish_agent(session, lease, result):
    job, now = job_service._live(session, lease)
    if job is None:
        return False
    row = session.scalar(select(AgentRun).where(AgentRun.job_id == job.id).with_for_update())
    reviewed = result.get("_review_completed")
    if reviewed:
        from .report_service import checked_resume, save_final
        saved_result = result["result"]
        decision = checked_resume(session, row, saved_result, reviewed)
        if decision.decision == "approved":
            check_result(session, row, saved_result)
            from .report_contract import validate_one_click
            validate_one_click(session, principal_for(session, row), row, saved_result)
        report = save_final(session, row, decision)
        add_event(session, row, "completed", {"status": decision.decision, "report_id": str(report.id) if report else None})
        return job_service.finish_job(session, lease, {"run_id": str(row.id), "status": decision.decision,
            "report_id": str(report.id) if report else None, "validated": decision.decision == "approved"})
    waiting = result.get("_waiting_review") is True
    if waiting:
        if row.workflow != "one_click" or row.recovery_version != "one-click-v1":
            raise ServiceError(409, "AGENT_CHECKPOINT_INVALID", "此分析未启用待审核恢复流程。")
        result = result["result"]
    check_result(session, row, result)
    if row.workflow == "one_click":
        from .report_contract import validate_one_click
        validate_one_click(session, principal_for(session, row), row, result)
        add_event(session, row, "report_validated", {"candidate_id": str(row.id), "status": "unreviewed"})
    row.result = result
    if waiting:
        from .recovery_models import GraphCheckpoint, GraphWrite
        from .job_models import JobAttempt
        if (row.checkpoint_id is None or session.get(GraphCheckpoint, (row.id, row.checkpoint_id)) is None
                or session.scalar(select(GraphWrite.run_id).where(GraphWrite.run_id == row.id,
                    GraphWrite.checkpoint_id == row.checkpoint_id, GraphWrite.channel == "__interrupt__").limit(1)) is None):
            raise ServiceError(409, "AGENT_CHECKPOINT_INVALID", "待审核暂停缺少对应的持久化图状态或中断记录。")
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.lease_token == lease.token))
        attempt.status = "paused"; attempt.stage = "waiting_review"; attempt.finished_at = now
        job.status = "waiting_review"; job.stage = "waiting_review"; job.progress = 95
        job.lease_token = job.lease_expires_at = job.worker_id = None
        row.review_started_at = now
        add_event(session, row, "review_pending", {"candidate_id": str(row.id), "checkpoint_id": row.checkpoint_id})
        audit(session, row.owner_id, "agent.review_pending", "agent_run", row.id, job.request_id,
            {"checkpoint_id": row.checkpoint_id})
        return True
    add_event(session, row, "completed", {"status": result["status"], "validation": "passed"})
    return job_service.finish_job(session, lease, {"run_id": str(row.id), "status": result["status"], "validated": True})


def citation_source(session, row, claim_index, citation_index):
    if session.get(Job, row.job_id).status not in ("succeeded", "waiting_review") or row.result is None:
        raise ServiceError(409, "AGENT_RESULT_UNAVAILABLE", "分析尚无已保存结果。")
    claims = (row.result.get("knowledge") or {}).get("claims", [])
    if not 0 <= claim_index < len(claims) or not 0 <= citation_index < len(claims[claim_index]["citations"]):
        raise ServiceError(404, "QA_CITATION_NOT_FOUND", "分析引用不存在。")
    from .qa_source import checked_citation_source
    return checked_citation_source(session, claims[claim_index]["citations"][citation_index])


def path_source(session, row, path_index, edge_index):
    """本人已保存路径按当时引文回查，关系后来撤回也不替换历史原文。"""
    if session.get(Job, row.job_id).status != "succeeded" or row.result is None:
        raise ServiceError(409, "AGENT_RESULT_UNAVAILABLE", "路径分析尚无已保存结果。")
    paths = (row.result.get("evidence_paths") or {}).get("paths", [])
    if not 0 <= path_index < len(paths) or not 0 <= edge_index < len(paths[path_index]["edges"]):
        raise ServiceError(404, "GRAPH_PATH_NOT_FOUND", "该分析的路径或关系不存在。")
    edge = paths[path_index]["edges"][edge_index]
    from .qa_source import checked_citation_source
    from .graph_service import get_relation, current_sources, available
    relation = get_relation(session, UUID(edge["relation_id"]))
    _, allowed = current_sources(session)
    return {**checked_citation_source(session, edge["citation"]),
        "relation_status": relation.status, "relation_revision": relation.revision,
        "relation_usable": relation.status == "approved" and available(relation, allowed),
        "saved_revision": edge["revision"]}
