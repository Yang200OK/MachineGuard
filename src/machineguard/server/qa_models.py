"""问答和会话固定快照、原问题、配置和所属用户。"""

from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, ForeignKey, String, Text, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class KnowledgeConversation(Base):
    __tablename__ = "knowledge_conversations"
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(100))
    equipment_type: Mapped[str | None] = mapped_column(String(200))
    device_context: Mapped[dict | None] = mapped_column(JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeAnswer(Base):
    __tablename__ = "knowledge_answers"
    __table_args__ = (UniqueConstraint("conversation_id", "turn_number", name="uq_answer_conversation_turn"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"))
    question: Mapped[str] = mapped_column(Text)
    equipment_type: Mapped[str | None] = mapped_column(String(200))
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_conversations.id", ondelete="RESTRICT"), index=True)
    turn_number: Mapped[int | None] = mapped_column(Integer)
    context_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_sha256: Mapped[str] = mapped_column(String(64))
    trace: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
