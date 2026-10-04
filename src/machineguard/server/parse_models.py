"""解析任务快照、父页面、切片及按身份隔离的请求键。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class DocumentParse(Base):
    __tablename__ = "document_parses"
    __table_args__ = (
        CheckConstraint("mode IN ('native','vision')", name="ck_parse_mode"),
        CheckConstraint("(completed_at IS NULL AND result_sha256 IS NULL AND page_count IS NULL AND chunk_count IS NULL) OR (completed_at IS NOT NULL AND result_sha256 IS NOT NULL AND page_count IS NOT NULL AND chunk_count IS NOT NULL AND page_count > 0 AND chunk_count > 0)", name="ck_parse_result"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    mode: Mapped[str] = mapped_column(String(8))
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_metadata: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("parse_id", "number", name="uq_parse_page"),
        CheckConstraint("number > 0 AND char_start >= 0 AND char_end >= char_start", name="ck_parse_page_limits"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    parse_id: Mapped[UUID] = mapped_column(ForeignKey("document_parses.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    extraction: Mapped[dict] = mapped_column(JSONB)


class DocumentChunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("parse_id", "ordinal", name="uq_parse_chunk"),
        CheckConstraint("ordinal > 0 AND token_count > 0 AND token_count <= 700 AND char_start >= 0 AND char_end > char_start AND page > 0", name="ck_parse_chunk_limits"))
    id: Mapped[UUID] = mapped_column(primary_key=True)
    parse_id: Mapped[UUID] = mapped_column(ForeignKey("document_parses.id", ondelete="RESTRICT"), index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    document_version: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)
    page_spans: Mapped[list] = mapped_column(JSONB)
    section: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(8))
    source_metadata: Mapped[dict] = mapped_column(JSONB)


class DocumentParseRequest(Base):
    __tablename__ = "document_parse_requests"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_parse_request"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    parse_id: Mapped[UUID] = mapped_column(ForeignKey("document_parses.id", ondelete="RESTRICT"))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
