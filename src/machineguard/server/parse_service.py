"""持久化解析提交、共享管理查询及与有效 Worker 租约原子提交结果。"""

from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select, text

from machineguard.parsing.config import load_parsing_configuration
from .auth_service import ServiceError, audit
from .document_models import DocumentVersion
from .document_service import authorize
from .job_models import Job
from .job_service import db_now, finish_job
from .parse_models import DocumentChunk, DocumentPage, DocumentParse, DocumentParseRequest


def parse_view(session, row):
    from .knowledge_service import review_state
    from .index_service import publication_state
    job = session.get(Job, row.job_id)
    return {"parse_id": str(row.id), "version_id": str(row.version_id), "job_id": str(row.job_id),
        "mode": row.mode, "status": job.status, "stage": job.stage, "progress": job.progress,
        "created_at": row.created_at.isoformat(), "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "page_count": row.page_count, "chunk_count": row.chunk_count, "result_sha256": row.result_sha256,
        "source_sha256": row.source_sha256, "config_sha256": row.config_sha256,
        "tokenizer": row.config_snapshot["tokenizer"], "chunk_tokens": row.config_snapshot["chunk_tokens"],
        "overlap_tokens": row.config_snapshot["overlap_tokens"], "attempt_count": job.attempt_count,
        "model": row.config_snapshot["vision_model"] if row.mode == "vision" else None,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        **review_state(session, row), "knowledge_status": publication_state(session, row.version_id, row.id)}


def latest_parse(session, version_id):
    row = session.scalar(select(DocumentParse).where(DocumentParse.version_id == version_id)
        .order_by(DocumentParse.created_at.desc(), DocumentParse.id.desc()).limit(1))
    return parse_view(session, row) if row else None


def submit_parse(session, principal, version_id, request, worker_config, request_id):
    authorize(session, principal)
    key = UUID(request.idempotency_key)
    fingerprint = sha256(f"{version_id}:{request.mode}".encode()).hexdigest()
    lock = int.from_bytes(sha256(f"jobs:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    receipt = session.scalar(select(DocumentParseRequest).where(DocumentParseRequest.owner_id == principal.user.id,
        DocumentParseRequest.idempotency_key == key))
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise ServiceError(409, "PARSE_IDEMPOTENCY_CONFLICT", "相同解析请求键已绑定不同版本或模式。")
        return {**parse_view(session, session.get(DocumentParse, receipt.parse_id)), "replayed": True, "deduplicated": False}, True
    version = session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
    if version is None:
        raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
    if version.status != "uploaded":
        raise ServiceError(409, "PARSE_ORIGINAL_UNAVAILABLE", "只有已确认保存的原文版本可以解析。")
    if not version.is_active:
        raise ServiceError(409, "DOCUMENT_VERSION_INACTIVE", "该版本已停用，不能新增解析任务。")
    if request.mode == "vision" and version.file_format != "pdf":
        raise ServiceError(422, "PARSE_MODE_INVALID", "视觉解析只接受 PDF。")
    config = load_parsing_configuration(); snapshot, config_digest = config.frozen()
    runs = session.scalars(select(DocumentParse).where(DocumentParse.version_id == version_id)
        .order_by(DocumentParse.created_at.desc())).all()
    row = None
    for candidate in runs:
        job = session.get(Job, candidate.job_id)
        same = candidate.mode == request.mode and candidate.config_sha256 == config_digest
        if job.status in ("queued", "running"):
            if not same:
                raise ServiceError(409, "PARSE_ALREADY_RUNNING", "该版本已有另一配置的解析任务，请先等待或取消。")
            row = candidate; break
        if same and job.status == "succeeded":
            row = candidate; break
    deduplicated = row is not None
    if row is None:
        pending = session.scalar(select(func.count()).select_from(Job).where(Job.owner_id == principal.user.id,
            Job.status.in_(["queued", "running"])))
        if pending >= worker_config.max_pending_per_owner:
            raise ServiceError(429, "JOB_LIMIT_REACHED", "待处理任务数量已达到上限。")
        now = db_now(session); parse_id, job_id = uuid4(), uuid4()
        payload = {"parse_id": str(parse_id), "version_id": str(version.id), "source_sha256": version.sha256,
            "config_sha256": config_digest, "mode": request.mode}
        job = Job(id=job_id, owner_id=principal.user.id, kind="document_parse" if request.mode == "native" else "document_visual_parse",
            payload=payload, idempotency_key="document-parse:" + str(key), request_fingerprint=fingerprint,
            request_id=request_id, created_at=now, deadline_at=now + timedelta(seconds=config.timeout_seconds),
            max_attempts=worker_config.max_attempts)
        session.add(job); session.flush()
        row = DocumentParse(id=parse_id, version_id=version.id, job_id=job_id, created_by=principal.user.id,
            mode=request.mode, config_snapshot=snapshot, config_sha256=config_digest, source_sha256=version.sha256,
            source_metadata=version.source_metadata, created_at=now)
        session.add(row); session.flush()
        audit(session, principal.user.id, "document.parse_submitted", "document_parse", row.id, request_id,
            {"version_id": str(version.id), "mode": request.mode, "config_sha256": config_digest})
    session.add(DocumentParseRequest(owner_id=principal.user.id, idempotency_key=key, parse_id=row.id,
        request_fingerprint=fingerprint, request_id=request_id))
    return {**parse_view(session, row), "replayed": False, "deduplicated": deduplicated}, deduplicated


def _parse(session, principal, parse_id):
    authorize(session, principal)
    row = session.get(DocumentParse, parse_id)
    if row is None:
        raise ServiceError(404, "PARSE_NOT_FOUND", "解析记录不存在。")
    return row


def parse_detail(session, principal, parse_id):
    return parse_view(session, _parse(session, principal, parse_id))


def parse_content(session, principal, parse_id, kind, offset, limit):
    row = _parse(session, principal, parse_id)
    if row.completed_at is None or session.get(Job, row.job_id).status != "succeeded":
        raise ServiceError(409, "PARSE_RESULT_UNAVAILABLE", "解析尚未成功，不提供半成品页面或切片。")
    model, ordering = (DocumentPage, DocumentPage.number) if kind == "pages" else (DocumentChunk, DocumentChunk.ordinal)
    rows = session.scalars(select(model).where(model.parse_id == row.id).order_by(ordering).offset(offset).limit(limit)).all()
    if kind == "pages":
        items = [{"page_id": str(value.id), "page": value.number, "text": value.text, "char_start": value.char_start,
            "char_end": value.char_end, "extraction": value.extraction} for value in rows]
        total = row.page_count
    else:
        items = [{"chunk_id": str(value.id), "source_id": str(value.source_id), "document_version": str(value.document_version),
            "ordinal": value.ordinal, "text": value.text, "sha256": value.sha256, "token_count": value.token_count,
            "char_start": value.char_start, "char_end": value.char_end, "page": value.page, "page_spans": value.page_spans,
            "section": value.section, "language": value.language, "equipment_type": value.source_metadata["equipment_type"],
            "applicable_scope": value.source_metadata["applicable_scope"]} for value in rows]
        total = row.chunk_count
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def finish_parse(session, lease, result):
    run = session.get(DocumentParse, UUID(lease.payload["parse_id"]))
    if run is None or run.job_id != lease.job_id:
        raise ServiceError(409, "PARSE_TARGET_INVALID", "解析记录与任务身份不一致。")
    if run.completed_at is not None:
        return False
    version = session.scalar(select(DocumentVersion).where(DocumentVersion.id == run.version_id).with_for_update())
    if version is None or version.status != "uploaded" or not version.is_active or version.sha256 != run.source_sha256:
        raise ServiceError(409, "PARSE_SOURCE_CHANGED", "解析目标或来源状态发生变化，不能提交。")
    pages, chunks = result["pages"], result["chunks"]
    summary = {"parse_id": str(run.id), "version_id": str(run.version_id), "page_count": len(pages), "chunk_count": len(chunks),
        "result_sha256": result["result_sha256"], "tokenizer": run.config_snapshot["tokenizer"],
        "review_status": "pending", "knowledge_status": "unpublished"}
    # 有效租约/取消/权限检查和页面、切片、任务终态共用一个事务，失败不会留下可读半成品。
    if not finish_job(session, lease, result=summary):
        return False
    run.completed_at = db_now(session); run.page_count = len(pages); run.chunk_count = len(chunks); run.result_sha256 = result["result_sha256"]
    for page in pages:
        extraction = {key: page[key] for key in ["blocks", "warnings", "adapter", "model", "usage"]}
        session.add(DocumentPage(id=uuid5(run.id, "page:" + str(page["number"])), parse_id=run.id,
            number=page["number"], text=page["text"], char_start=page["char_start"], char_end=page["char_end"], extraction=extraction))
    for chunk in chunks:
        session.add(DocumentChunk(id=uuid5(run.id, "chunk:" + str(chunk["ordinal"])), parse_id=run.id,
            source_id=version.document_id, document_version=version.id, source_metadata=run.source_metadata, **chunk))
    audit(session, run.created_by, "document.parsed", "document_parse", run.id, session.get(Job, lease.job_id).request_id,
        {"page_count": len(pages), "chunk_count": len(chunks), "result_sha256": result["result_sha256"]})
    return True
