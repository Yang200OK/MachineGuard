"""不可变知识快照、向量和固定分词结果；发布指针单独管理。"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class KnowledgeSnapshot(Base):
    __tablename__ = "knowledge_snapshots"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True)
    base_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_sha256: Mapped[str] = mapped_column(String(64))
    members: Mapped[list] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    usage: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (CheckConstraint("(published_at IS NULL AND manifest_sha256 IS NULL AND chunk_count IS NULL) OR (published_at IS NOT NULL AND manifest_sha256 IS NOT NULL AND chunk_count > 0)", name="ck_snapshot_publication"),)


class KnowledgeCatalog(Base):
    __tablename__ = "knowledge_catalog"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("id = 1 AND revision >= 0", name="ck_catalog_singleton"),)


class KnowledgePublishRequest(Base):
    __tablename__ = "knowledge_publish_requests"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_knowledge_publish_key"),)


class SnapshotChunk(Base):
    __tablename__ = "snapshot_chunks"
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"), primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunks.id", ondelete="RESTRICT"), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    source_metadata: Mapped[dict] = mapped_column(JSONB)
    location: Mapped[dict] = mapped_column(JSONB)
    tokens: Mapped[list] = mapped_column(JSONB)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
