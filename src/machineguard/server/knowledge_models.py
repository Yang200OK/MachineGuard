"""来源纠正与发布前审核单独留痕，不改写上传及解析快照。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class DocumentMetadataRevision(Base):
    __tablename__ = "document_metadata_revisions"
    __table_args__ = (UniqueConstraint("version_id", "number", name="uq_document_metadata_revision"),
                     CheckConstraint("number > 0", name="ck_metadata_revision_number"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    source_metadata: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(1000))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentManagementAction(Base):
    __tablename__ = "document_management_actions"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_document_management_key"),
        CheckConstraint("kind IN ('metadata','deactivate','review')", name="ck_document_management_kind"),
        CheckConstraint("metadata_revision >= 0 AND ((kind = 'review' AND parse_id IS NOT NULL AND decision IS NOT NULL AND decision IN ('approved','rejected') AND result_sha256 IS NOT NULL) OR (kind <> 'review' AND parse_id IS NULL AND decision IS NULL AND result_sha256 IS NULL))", name="ck_document_management_review"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True)
    parse_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_parses.id", ondelete="RESTRICT"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str | None] = mapped_column(String(16))
    metadata_revision: Mapped[int] = mapped_column(Integer)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
