"""人工决定和正式报告分别保存；同一运行最多各一条。"""

from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class ReportReview(Base):
    __tablename__ = "report_reviews"
    __table_args__ = (CheckConstraint("decision IN ('approved','rejected')", name="ck_report_review_decision"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"), primary_key=True)
    id: Mapped[UUID] = mapped_column(default=uuid4, unique=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(Text)
    candidate_sha256: Mapped[str] = mapped_column(String(64))
    checkpoint_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinalReport(Base):
    __tablename__ = "final_reports"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"), unique=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    review_id: Mapped[UUID] = mapped_column(ForeignKey("report_reviews.id", ondelete="RESTRICT"), unique=True)
    prediction_id: Mapped[UUID] = mapped_column(ForeignKey("predictions.id", ondelete="RESTRICT"))
    content: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
