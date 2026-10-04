"""文档、不可变原文版本和上传请求的持久化映射。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class Document(Base):
    __tablename__ = "documents"
    category: Mapped[str] = mapped_column(String(40), server_default="知识助手与管理")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("created_by", "creation_fingerprint", name="uq_document_creation"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    creation_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_document_version_number"),
        UniqueConstraint("document_id", "fingerprint", name="uq_document_version_fingerprint"),
        CheckConstraint("number > 0 AND attempt_count >= 0 AND size_bytes > 0 AND size_bytes <= 10485760", name="ck_document_version_limits"),
        CheckConstraint("status IN ('uploading','uploaded','failed')", name="ck_document_version_status"),
        CheckConstraint("file_format IN ('pdf','md','txt')", name="ck_document_version_format"),
        CheckConstraint("(status = 'uploading' AND upload_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'uploading' AND upload_token IS NULL AND lease_expires_at IS NULL)", name="ck_document_version_lease"),
        CheckConstraint("(status = 'uploaded' AND uploaded_at IS NOT NULL AND etag IS NOT NULL AND error_code IS NULL) OR (status <> 'uploaded' AND uploaded_at IS NULL AND etag IS NULL)", name="ck_document_version_result"),
        Index("ix_document_versions_document_id", "document_id"),
        CheckConstraint("management_revision >= 0", name="ck_document_management_revision"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    number: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source_metadata: Mapped[dict] = mapped_column(JSONB)
    filename: Mapped[str] = mapped_column(String(180))
    file_format: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    oss_bucket: Mapped[str] = mapped_column(String(63))
    oss_region: Mapped[str] = mapped_column(String(40))
    object_key: Mapped[str] = mapped_column(String(256), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    upload_token: Mapped[UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    management_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DocumentUploadRequest(Base):
    __tablename__ = "document_upload_requests"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_document_upload_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    target_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"))
    request_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
