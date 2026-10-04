"""原问题与知识快照固定的单轮问答；请求重放只读取原结果。"""

from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4
from sqlalchemy import func, select, text
from .auth_service import Principal, ServiceError, audit
from .document_models import DocumentVersion
from .document_service import authorize
from .index_config import digest
from .index_models import KnowledgeCatalog, KnowledgeSnapshot
from .index_service import member_valid
from .job_models import Job
from .job_service import db_now, finish_job
from .models import SessionRecord, User
from .qa_config import load_qa_configuration
from .qa_models import KnowledgeAnswer


def principal_for(session, row):
    record = session.get(SessionRecord, row.session_id)
    user = session.get(User, row.owner_id)
    if record is None or user is None or record.user_id != row.owner_id:
        raise ServiceError(401, "SESSION_INVALID", "问答任务身份依据无效。")
    principal = Principal(user, record)
    authorize(session, principal)
    return principal


def answer_view(session, row):
    job = session.get(Job, row.job_id)
    return {"answer_id": str(row.id), "job_id": str(row.job_id), "snapshot_id": str(row.snapshot_id),
        "question": row.question, "equipment_type": row.equipment_type, "created_at": row.created_at.isoformat(),
        "conversation_id": str(row.conversation_id) if row.conversation_id else None, "turn_number": row.turn_number,
        "context_snapshot": row.context_snapshot,
        "job_status": job.status, "stage": job.stage, "progress": job.progress,
        "config_snapshot": row.config_snapshot, "config_sha256": row.config_sha256,
        "result": row.result if job.status == "succeeded" else None, "trace": row.trace,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None}


def owner_lock(session, principal):
    lock = int.from_bytes(sha256(f"jobs:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    authorize(session, principal)


def submit_answer(session, principal, payload, worker_config, request_id):
    authorize(session, principal)
    body = payload.model_dump(mode="json")
    # 新可选字段为空时沿用旧指纹，旧单轮请求仍可原样确认。
    if payload.conversation_id is None:
        body.pop("conversation_id"); body.pop("expected_revision")
    fingerprint = digest(body)
    key = "qa:" + str(payload.idempotency_key)
    owner_lock(session, principal)
    existing = session.scalar(select(Job).where(Job.owner_id == principal.user.id, Job.idempotency_key == key))
    if existing:
        if existing.kind != "knowledge_answer" or existing.request_fingerprint != fingerprint:
            raise ServiceError(409, "QA_IDEMPOTENCY_CONFLICT", "此问答请求键已绑定另一组问题、快照或范围。")
        row = session.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.job_id == existing.id))
        owned_answer(session, principal, row.id)
        return {**answer_view(session, row), "replayed": True}
    conversation = None; context = None; conversation_config = None
    if payload.conversation_id is not None:
        from .conversation_service import prepare_turn
        conversation, context, conversation_config = prepare_turn(session, principal, payload)
    snapshot_id = conversation.snapshot_id if conversation else payload.snapshot_id or session.get(KnowledgeCatalog, 1).current_id
    snapshot = session.get(KnowledgeSnapshot, snapshot_id) if snapshot_id else None
    if snapshot is None or snapshot.published_at is None:
        raise ServiceError(409, "KNOWLEDGE_NOT_PUBLISHED", "请先发布完整知识快照。")
    pending = session.scalar(select(func.count()).select_from(Job).where(Job.owner_id == principal.user.id,
        Job.status.in_(["queued", "running"])))
    if pending >= worker_config.max_pending_per_owner:
        raise ServiceError(429, "JOB_LIMIT_REACHED", "待处理任务已达到上限。")
    config = load_qa_configuration(); frozen, config_hash = config.frozen()
    now = db_now(session); answer_id = uuid4()
    job = Job(owner_id=principal.user.id, kind="knowledge_answer", payload={"answer_id": str(answer_id),
        "snapshot_id": str(snapshot.id)}, idempotency_key=key, request_fingerprint=fingerprint,
        request_id=request_id, created_at=now, deadline_at=now + timedelta(
            seconds=conversation_config.timeout_seconds if conversation_config else config.timeout_seconds), max_attempts=1)
    session.add(job); session.flush()
    row = KnowledgeAnswer(id=answer_id, job_id=job.id, owner_id=principal.user.id, session_id=principal.session.id,
        snapshot_id=snapshot.id, question=payload.query, equipment_type=conversation.equipment_type if conversation else payload.equipment_type,
        conversation_id=conversation.id if conversation else None, turn_number=conversation.revision if conversation else None,
        context_snapshot=context,
        config_snapshot=frozen, config_sha256=config_hash, created_at=now)
    session.add(row); session.flush()
    audit(session, principal.user.id, "knowledge.answer_submitted", "knowledge_answer", row.id, request_id,
        {"snapshot_id": str(snapshot.id), "config_sha256": config_hash})
    return {**answer_view(session, row), "replayed": False}


def owned_answer(session, principal, answer_id):
    authorize(session, principal)
    row = session.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.id == answer_id, KnowledgeAnswer.owner_id == principal.user.id))
    if row is None:
        raise ServiceError(404, "QA_NOT_FOUND", "问答记录不存在或不属于当前身份。")
    if row.conversation_id is not None:
        from .conversation_service import owned_conversation
        owned_conversation(session, principal, row.conversation_id)
    return row


def list_answers(session, principal, offset, limit):
    authorize(session, principal)
    query = select(KnowledgeAnswer).where(KnowledgeAnswer.owner_id == principal.user.id)
    from .qa_models import KnowledgeConversation
    query = query.outerjoin(KnowledgeConversation, KnowledgeAnswer.conversation_id == KnowledgeConversation.id).where(
        (KnowledgeAnswer.conversation_id.is_(None)) | KnowledgeConversation.deleted_at.is_(None))
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(KnowledgeAnswer.created_at.desc(), KnowledgeAnswer.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [answer_view(session, row) for row in rows], "total": count, "offset": offset, "limit": limit}


def assert_evidence_current(session, row, evidence, lock=False):
    principal_for(session, row)
    snapshot = session.get(KnowledgeSnapshot, row.snapshot_id)
    for version_id in sorted({item["version_id"] for item in evidence}):
        query = select(DocumentVersion).where(DocumentVersion.id == UUID(version_id))
        if lock:
            query = query.with_for_update()
        session.scalar(query.execution_options(populate_existing=True))
        member = next((m for m in snapshot.members if m["version_id"] == version_id), None)
        if member is None or not member_valid(session, member):
            raise ServiceError(409, "QA_SOURCE_CHANGED", "问答期间来源停用、纠正或批准依据变化，未发布候选回答。")


def finish_answer(session, lease, result):
    row = session.get(KnowledgeAnswer, UUID(lease.payload["answer_id"]))
    if row is None or row.job_id != lease.job_id or row.result is not None:
        raise ServiceError(409, "QA_TARGET_INVALID", "问答目标与任务不一致或已提交。")
    from .conversation_service import validate_context
    validate_context(session, principal_for(session, row), row)
    historical = [quote for turn in row.context_snapshot["history"] for quote in turn["quotes"]] if row.context_snapshot else []
    # 历史来源和本轮证据按同一版本顺序加锁，防止核对后来源撤回及交叉锁顺序冲突。
    assert_evidence_current(session, row, result["evidence"] + historical, lock=True)
    if not finish_job(session, lease, result={"answer_id": str(row.id), "snapshot_id": str(row.snapshot_id),
        "answer_status": result["status"], "citation_count": sum(len(c["citations"]) for c in result["claims"])}):
        return False
    row.result = result
    audit(session, row.owner_id, "knowledge.answer_completed", "knowledge_answer", row.id,
          session.get(Job, lease.job_id).request_id, {"status": result["status"], "snapshot_id": str(row.snapshot_id)})
    return True
