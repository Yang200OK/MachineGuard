"""文档上传服务：授权、来源冻结、幂等、内容去重与 OSS/数据库结果协调。"""

from datetime import timedelta
from hashlib import sha256
import json
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .auth_service import Principal, ServiceError, audit
from .document_models import Document, DocumentUploadRequest, DocumentVersion
from .document_schemas import SourceMetadata
from .models import SessionRecord, User
from .oss_storage import MAX_FILE_BYTES, OssStorage


UPLOAD_LEASE_SECONDS = 120
UPLOAD_MAX_ATTEMPTS = 3
CONTENT_TYPES = {"pdf": "application/pdf", "md": "text/markdown; charset=utf-8", "txt": "text/plain; charset=utf-8"}


def authorize(session: Session, principal: Principal):
    """服务层也执行授权，提交上传结果前重新核对账号与会话。"""
    user = session.scalar(select(User).where(User.id == principal.user.id).execution_options(populate_existing=True))
    record = session.scalar(select(SessionRecord).where(SessionRecord.id == principal.session.id).execution_options(populate_existing=True))
    now = session.scalar(select(func.clock_timestamp()))
    if user is None or not user.is_active or record is None or record.revoked_at is not None or record.expires_at <= now:
        raise ServiceError(401, "SESSION_INVALID", "当前会话已失效，不能提交文档结果。")
    Principal(user, record).require("knowledge:manage")
    if user.is_guest:
        raise ServiceError(403, "PERMISSION_DENIED", "访客不能管理知识文档。")


def check_file(filename: str, data: bytes):
    if not filename or len(filename) > 180 or any(c in filename for c in '/\\:') or any(ord(c) < 32 for c in filename) or filename in (".", ".."):
        raise ServiceError(422, "DOCUMENT_FILENAME_INVALID", "文件名必须为不含路径的普通文件名。")
    suffix = PurePosixPath(filename).suffix.lower().removeprefix(".")
    if suffix not in CONTENT_TYPES:
        raise ServiceError(422, "DOCUMENT_FORMAT_INVALID", "仅允许 PDF、Markdown（.md）和 TXT。")
    if not data:
        raise ServiceError(422, "DOCUMENT_EMPTY", "不能上传空文件。")
    if len(data) > MAX_FILE_BYTES:
        raise ServiceError(413, "DOCUMENT_TOO_LARGE", "单份原文不能超过 10 MiB。")
    if suffix == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ServiceError(422, "DOCUMENT_FORMAT_INVALID", "PDF 文件头不合法。")
    else:
        try:
            value = data.decode("utf-8-sig", errors="strict")
        except UnicodeError:
            raise ServiceError(422, "DOCUMENT_ENCODING_INVALID", "Markdown 和 TXT 必须为 UTF-8。") from None
        if not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ServiceError(422, "DOCUMENT_TEXT_INVALID", "文本为空或含二进制控制字符。")
    return suffix


def version_view(session: Session, row: DocumentVersion):
    # 延迟导入只为避免服务间循环导入；解析状态从数据库任务读取，不以原文状态推断。
    from .parse_service import latest_parse
    from .knowledge_service import version_management
    from .index_service import publication_state
    parsed = latest_parse(session, row.id)
    return {"version_id": str(row.id), "document_id": str(row.document_id), "number": row.number,
        "source_metadata": row.source_metadata, "filename": row.filename, "file_format": row.file_format,
        "size_bytes": row.size_bytes, "sha256": row.sha256, "status": row.status,
        "attempt_count": row.attempt_count, "max_attempts": UPLOAD_MAX_ATTEMPTS,
        "created_at": row.created_at.isoformat(), "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "error": {"code": row.error_code, "message": row.error_message} if row.error_code else None,
        "parse_status": parsed["status"] if parsed else "not_started", "latest_parse": parsed,
        "knowledge_status": publication_state(session, row.id), **version_management(session, row)}


def _document(session: Session, document_id: UUID, lock=False):
    query = select(Document).where(Document.id == document_id)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise ServiceError(404, "DOCUMENT_NOT_FOUND", "文档不存在。")
    return row


def document_detail(session: Session, principal: Principal, document_id: UUID, offset=0, limit=20):
    authorize(session, principal)
    doc = _document(session, document_id)
    rows = session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.number.desc()).offset(offset).limit(limit)).all()
    count = session.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == doc.id))
    latest = session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc.id, DocumentVersion.status == "uploaded")
        .order_by(DocumentVersion.number.desc()).limit(1))
    return {"document_id": str(doc.id), "title": doc.title, "created_at": doc.created_at.isoformat(),
        "category": doc.category, "archived_at": doc.archived_at,
        "latest_uploaded_version_id": str(latest.id) if latest else None,
        "versions": {"items": [version_view(session, row) for row in rows], "total": count, "offset": offset, "limit": limit}}


def list_documents(session: Session, principal: Principal, offset: int, limit: int):
    authorize(session, principal)
    rows = session.scalars(select(Document).order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit)).all()
    count = session.scalar(select(func.count()).select_from(Document))
    items = []
    for row in rows:
        latest = session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == row.id).order_by(DocumentVersion.number.desc()).limit(1))
        items.append({"document_id": str(row.id), "title": row.title, "created_at": row.created_at.isoformat(),
                      "latest_version": version_view(session, latest)})
    return {"items": items, "total": count, "offset": offset, "limit": limit}


def _fingerprint(filename: str, suffix: str, digest: str, metadata: SourceMetadata):
    body = {"filename": filename, "file_format": suffix, "sha256": digest, "source_metadata": metadata.model_dump()}
    return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def upload(session: Session, principal: Principal, filename: str, data: bytes, metadata: SourceMetadata,
           key: UUID, request_id: UUID, target_document_id: UUID | None = None):
    authorize(session, principal)
    suffix = check_file(filename, data)
    digest = sha256(data).hexdigest()
    fingerprint = _fingerprint(filename, suffix, digest, metadata)
    # 请求键按身份隔离；同一身份的创建与别名请求串行化，跨管理员用文档行锁分配版本号。
    lock = int.from_bytes(sha256(("document-upload:" + str(principal.user.id)).encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    receipt = session.scalar(select(DocumentUploadRequest).where(DocumentUploadRequest.owner_id == principal.user.id,
        DocumentUploadRequest.idempotency_key == key))
    replayed = receipt is not None
    deduplicated = False
    store = None
    if receipt is not None:
        if receipt.target_document_id != target_document_id or receipt.fingerprint != fingerprint:
            raise ServiceError(409, "DOCUMENT_IDEMPOTENCY_CONFLICT", "相同请求键的目标、文件或来源元数据不一致。")
        row = session.scalar(select(DocumentVersion).where(DocumentVersion.id == receipt.version_id).with_for_update())
    else:
        if target_document_id is None:
            doc = session.scalar(select(Document).where(Document.created_by == principal.user.id,
                Document.creation_fingerprint == fingerprint).with_for_update())
            if doc is None:
                doc = Document(title=metadata.title, created_by=principal.user.id, creation_fingerprint=fingerprint)
                session.add(doc)
                session.flush()
        else:
            doc = _document(session, target_document_id, lock=True)
        row = session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc.id,
            DocumentVersion.fingerprint == fingerprint).with_for_update())
        deduplicated = row is not None
        if row is None:
            store = OssStorage()
            count = session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.created_by == principal.user.id, DocumentVersion.status == "uploading"))
            if count >= 10:
                raise ServiceError(429, "DOCUMENT_UPLOAD_LIMIT", "未确认的上传超过限额，请先处理已有上传。")
            number = session.scalar(select(func.coalesce(func.max(DocumentVersion.number), 0)).where(DocumentVersion.document_id == doc.id)) + 1
            version_id = uuid4()
            row = DocumentVersion(id=version_id, document_id=doc.id, number=number, created_by=principal.user.id,
                source_metadata=metadata.model_dump(), filename=filename, file_format=suffix, size_bytes=len(data),
                sha256=digest, fingerprint=fingerprint, oss_bucket=store.bucket, oss_region=store.settings.MG_OSS_REGION,
                object_key=f"{store.settings.MG_OSS_PREFIX}documents/{doc.id}/{version_id}.{suffix}",
                status="uploading", upload_token=uuid4(), lease_expires_at=session.scalar(select(func.clock_timestamp())) + timedelta(seconds=UPLOAD_LEASE_SECONDS),
                attempt_count=0)
            session.add(row)
            session.flush()
        session.add(DocumentUploadRequest(owner_id=principal.user.id, idempotency_key=key, target_document_id=target_document_id,
            fingerprint=fingerprint, version_id=row.id, request_id=request_id))
    if row.status == "uploaded":
        result = {**version_view(session, row), "replayed": replayed, "deduplicated": deduplicated}
        session.commit()
        return result, True
    now = session.scalar(select(func.clock_timestamp()))
    if row.attempt_count and row.status == "uploading" and row.lease_expires_at > now:
        raise ServiceError(409, "DOCUMENT_UPLOAD_IN_PROGRESS", "同一版本正在上传，请查询状态后用原请求键确认。")
    if row.attempt_count >= UPLOAD_MAX_ATTEMPTS:
        raise ServiceError(409, "DOCUMENT_UPLOAD_ATTEMPTS_EXHAUSTED", "该版本上传已达尝试上限，请根据失败原因处理。")
    row.status, row.upload_token = "uploading", uuid4()
    row.lease_expires_at = now + timedelta(seconds=UPLOAD_LEASE_SECONDS)
    row.attempt_count += 1
    row.error_code = row.error_message = None
    token, version_id, object_key, bucket, region = row.upload_token, row.id, row.object_key, row.oss_bucket, row.oss_region
    audit(session, principal.user.id, "document.upload_claimed", "document_version", row.id, request_id,
          {"document_id": str(row.document_id), "number": row.number, "sha256": digest, "attempt": row.attempt_count})
    # 先提交持久化领取，再写 OSS；进程退出或响应丢失仍能查到版本身份。
    session.commit()
    try:
        store = OssStorage() if store is None else store
        if store.bucket != bucket or store.settings.MG_OSS_REGION != region:
            raise ServiceError(503, "OSS_CONFIGURATION_CHANGED", "当前 OSS 与该版本冻结的 Bucket 或地域不一致。")
        store.check_bucket()
        etag = store.store_verified(object_key, data, CONTENT_TYPES[suffix], digest)
        row = session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
        now = session.scalar(select(func.clock_timestamp()))
        if row.upload_token != token or row.status != "uploading" or row.lease_expires_at <= now:
            raise ServiceError(409, "DOCUMENT_UPLOAD_LEASE_EXPIRED", "上传领取已失效，请用原请求键确认原文。")
        authorize(session, principal)
        row.status, row.etag, row.uploaded_at = "uploaded", etag, now
        row.upload_token = row.lease_expires_at = None
        audit(session, principal.user.id, "document.uploaded", "document_version", row.id, request_id,
              {"document_id": str(row.document_id), "number": row.number, "sha256": digest, "size_bytes": len(data)})
        result = {**version_view(session, row), "replayed": replayed, "deduplicated": deduplicated}
        session.commit()
        return result, replayed or deduplicated
    except ServiceError as error:
        session.rollback()
        row = session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
        # 旧领取者不能改写新领取者的状态；已写对象保留，后续显式确认时核对原文。
        if row.upload_token == token and row.status == "uploading":
            row.status = "failed"
            row.upload_token = row.lease_expires_at = None
            row.error_code, row.error_message = error.code, error.message
            audit(session, principal.user.id, "document.upload_failed", "document_version", row.id, request_id, {"code": error.code})
            session.commit()
        raise


def read_original(session: Session, principal: Principal, version_id: UUID):
    authorize(session, principal)
    row = session.get(DocumentVersion, version_id)
    if row is None:
        raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
    if row.status != "uploaded":
        raise ServiceError(409, "DOCUMENT_ORIGINAL_UNAVAILABLE", "该版本原文尚未确认上传成功。")
    store = OssStorage()
    if store.bucket != row.oss_bucket or store.settings.MG_OSS_REGION != row.oss_region:
        raise ServiceError(503, "OSS_CONFIGURATION_CHANGED", "OSS 配置与历史版本不一致。")
    store.check_bucket()
    data = store.read_verified(row.object_key, row.size_bytes, row.sha256)
    authorize(session, principal)
    return row.filename, row.file_format, data
