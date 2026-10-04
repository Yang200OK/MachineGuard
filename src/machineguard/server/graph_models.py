"""有来源的文档命名空间实体、关系候选和不可变管理动作。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class GraphEntity(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (UniqueConstraint("document_id", "name", name="uq_graph_entity_name"),
        CheckConstraint("length(name) > 0 AND length(kind) > 0", name="ck_graph_entity_name"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GraphRelation(Base):
    __tablename__ = "graph_relations"
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected','withdrawn')", name="ck_graph_relation_status"),
        CheckConstraint("revision >= 1 AND subject_id <> object_id", name="ck_graph_relation_revision"),
        CheckConstraint("length(predicate) > 0 AND jsonb_typeof(citation) = 'object' AND jsonb_typeof(source_basis) = 'object'", name="ck_graph_relation_basis"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("graph_entities.id", ondelete="RESTRICT"))
    object_id: Mapped[UUID] = mapped_column(ForeignKey("graph_entities.id", ondelete="RESTRICT"))
    predicate: Mapped[str] = mapped_column(String(80))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"))
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunks.id", ondelete="RESTRICT"), index=True)
    chunk_sha256: Mapped[str] = mapped_column(String(64))
    source_basis: Mapped[dict] = mapped_column(JSONB)
    citation: Mapped[dict] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    creator_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    note: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GraphAction(Base):
    __tablename__ = "graph_actions"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key", name="uq_graph_action_key"),
        CheckConstraint("kind IN ('create','review') AND revision >= 1", name="ck_graph_action_kind"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    relation_id: Mapped[UUID] = mapped_column(ForeignKey("graph_relations.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
