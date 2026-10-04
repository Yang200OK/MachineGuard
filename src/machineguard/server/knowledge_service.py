"""来源纠正、版本停用及发布前审核，共享行锁与持久化动作键。"""

from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from .auth_service import ServiceError, audit
from .document_models import DocumentVersion
from .document_service import authorize
from .job_models import Job
from .knowledge_models import DocumentManagementAction, DocumentMetadataRevision
from .models import User
from .parse_models import DocumentParse


def effective_metadata(session, version):
    revision = session.scalar(select(DocumentMetadataRevision).where(DocumentMetadataRevision.version_id == version.id)
        .order_by(DocumentMetadataRevision.number.desc()).limit(1))
    return (revision.source_metadata, revision.number) if revision else (version.source_metadata, 0)


def version_management(session, version):
    metadata, number = effective_metadata(session, version)
    return {"is_active": version.is_active, "management_revision": version.management_revision,
            "metadata_revision": number, "effective_source_metadata": metadata}


def review_state(session, parsed):
    """审核只覆盖明确的解析结果和来源修订，修改来源后原批准失效。"""
    version = session.get(DocumentVersion, parsed.version_id)
    job = session.get(Job, parsed.job_id)
    if not version.is_active:
        return {"review_status": "inactive", "review": None}
    if parsed.completed_at is None or job.status != "succeeded":
        return {"review_status": "not_ready", "review": None}
    row = session.scalar(select(DocumentManagementAction).where(DocumentManagementAction.parse_id == parsed.id,
        DocumentManagementAction.kind == "review").order_by(DocumentManagementAction.created_at.desc(),
        DocumentManagementAction.id.desc()).limit(1))
    if row is None:
        return {"review_status": "pending", "review": None}
    metadata, revision = effective_metadata(session, version)
    state = row.decision if row.metadata_revision == revision and row.result_sha256 == parsed.result_sha256 else "stale"
    return {"review_status": state, "review": action_view(session, row)}


def action_view(session, row):
    actor = session.get(User, row.owner_id)
    return {"action_id": str(row.id), "version_id": str(row.version_id),
        "parse_id": str(row.parse_id) if row.parse_id else None, "kind": row.kind, "decision": row.decision,
        "metadata_revision": row.metadata_revision, "result_sha256": row.result_sha256,
        "actor": actor.display_name, "created_at": row.created_at.isoformat(), "payload": row.payload,
        "result": row.result}


def management_history(session, principal, version_id, offset, limit):
    authorize(session, principal)
    version = session.get(DocumentVersion, version_id)
    if version is None:
        raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
    rows = session.scalars(select(DocumentManagementAction).where(DocumentManagementAction.version_id == version_id)
        .order_by(DocumentManagementAction.created_at.desc(), DocumentManagementAction.id.desc()).offset(offset).limit(limit)).all()
    count = session.scalar(select(func.count()).select_from(DocumentManagementAction).where(DocumentManagementAction.version_id == version_id))
    return {**version_management(session, version), "items": [action_view(session, row) for row in rows],
            "total": count, "offset": offset, "limit": limit}


def manage(session, principal, version_id, kind, request, request_id, parse_id=None):
    authorize(session, principal)
    payload = request.model_dump(mode="json")
    fingerprint = sha256(json.dumps({"version_id": str(version_id), "parse_id": str(parse_id) if parse_id else None,
        "kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # 同一身份的动作键串行化；版本行锁防止两名管理员覆盖彼此的纠正或审核。
    lock = int.from_bytes(sha256(f"document-management:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    authorize(session, principal)
    receipt = session.scalar(select(DocumentManagementAction).where(DocumentManagementAction.owner_id == principal.user.id,
        DocumentManagementAction.idempotency_key == UUID(request.idempotency_key)))
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise ServiceError(409, "KNOWLEDGE_IDEMPOTENCY_CONFLICT", "该动作键已绑定另一组管理参数。")
        return {**receipt.result, "replayed": True}
    version = session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
    if version is None:
        raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
    # 锁等待期间账号或会话可能被撤销，取得版本行锁后必须重新授权。
    authorize(session, principal)
    if version.management_revision != request.expected_revision:
        raise ServiceError(409, "KNOWLEDGE_REVISION_CONFLICT", "版本已被其他操作修改，请刷新后重新核对。")
    if not version.is_active:
        raise ServiceError(409, "DOCUMENT_VERSION_INACTIVE", "该版本已停用，不能新增解析、来源纠正或审核。")
    if version.status != "uploaded":
        raise ServiceError(409, "DOCUMENT_ORIGINAL_UNAVAILABLE", "只有原文已确认保存的版本可管理。")
    running = session.scalar(select(Job.id).join(DocumentParse, DocumentParse.job_id == Job.id)
        .where(DocumentParse.version_id == version.id, Job.status.in_(["queued", "running"])).limit(1))
    if running:
        raise ServiceError(409, "DOCUMENT_PARSE_BUSY", "该版本仍有待执行或运行中的解析，请等待结束或先取消。")
    metadata, metadata_number = effective_metadata(session, version)
    details = {"reason": request.reason}
    decision = result_hash = None
    if kind == "metadata":
        corrected = request.metadata.model_dump()
        if corrected == metadata:
            raise ServiceError(409, "DOCUMENT_METADATA_UNCHANGED", "来源元数据没有变化，无需创建纠正记录。")
        metadata_number += 1
        session.add(DocumentMetadataRevision(version_id=version.id, number=metadata_number,
            source_metadata=corrected, reason=request.reason, created_by=principal.user.id))
        details.update({"before": metadata, "after": corrected})
    elif kind == "deactivate":
        version.is_active = False
    elif kind == "review":
        parsed = session.get(DocumentParse, parse_id)
        if parsed is None or parsed.version_id != version.id:
            raise ServiceError(404, "PARSE_NOT_FOUND", "解析记录不存在或不属于该版本。")
        if parsed.completed_at is None or session.get(Job, parsed.job_id).status != "succeeded":
            raise ServiceError(409, "PARSE_RESULT_UNAVAILABLE", "只允许审核已成功保存的解析结果。")
        if request.metadata_revision != metadata_number or request.result_sha256 != parsed.result_sha256:
            raise ServiceError(409, "KNOWLEDGE_REVIEW_BASIS_CHANGED", "审核依据的来源修订或解析指纹已变化，请重新预览。")
        if request.decision == "approved":
            if not all((request.source_checked, request.content_checked, request.location_checked)):
                raise ServiceError(422, "KNOWLEDGE_REVIEW_INCOMPLETE", "批准前必须确认来源与许可、正文内容及页码位置。")
            if metadata["license"].lower() in {"unknown", "未知", "不明", "待确认"}:
                raise ServiceError(422, "KNOWLEDGE_LICENSE_UNCONFIRMED", "许可说明尚未确认，请先纠正来源信息。")
        decision, result_hash = request.decision, parsed.result_sha256
        details.update({"source_metadata": metadata, "source_checked": request.source_checked,
            "content_checked": request.content_checked, "location_checked": request.location_checked})
    else:
        raise ValueError("未知文档管理操作。")
    version.management_revision += 1
    action_id = uuid4()
    result = {"action_id": str(action_id), "version_id": str(version.id), "kind": kind,
        "parse_id": str(parse_id) if parse_id else None, "decision": decision,
        "management_revision": version.management_revision, "metadata_revision": metadata_number,
        "is_active": version.is_active, "knowledge_status": "unpublished"}
    session.add(DocumentManagementAction(id=action_id, version_id=version.id, parse_id=parse_id,
        owner_id=principal.user.id, idempotency_key=UUID(request.idempotency_key), request_fingerprint=fingerprint,
        request_id=request_id, kind=kind, decision=decision, metadata_revision=metadata_number,
        result_sha256=result_hash, payload=details, result=result, created_at=session.scalar(select(func.clock_timestamp()))))
    audit(session, principal.user.id, "document." + kind, "document_version", version.id, request_id,
        {"action_id": str(action_id), "revision": version.management_revision, "parse_id": str(parse_id) if parse_id else None})
    session.flush()
    return {**result, "replayed": False}
