"""正式报告的交付附件与模拟维护任务，不修改报告原文。"""

from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class ReportExport(Base):
    __tablename__ = "report_exports"
    __table_args__ = (CheckConstraint("format IN ('md','json','pdf')", name="ck_export_format"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("final_reports.id", ondelete="RESTRICT"), index=True)
    creator_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True)
    format: Mapped[str] = mapped_column(String(8))
    content_sha256: Mapped[str] = mapped_column(String(64))
    render_config: Mapped[dict] = mapped_column(JSONB)
    artifact: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaintenanceTask(Base):
    __tablename__ = "maintenance_tasks"
    __table_args__ = (CheckConstraint("status IN ('pending','in_progress','completed','cancelled')", name="ck_maintenance_status"),
                     CheckConstraint("revision >= 1", name="ck_maintenance_revision"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("final_reports.id", ondelete="RESTRICT"), unique=True)
    creator_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    creation_key: Mapped[UUID] = mapped_column()
    title: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaintenanceAction(Base):
    __tablename__ = "maintenance_actions"
    __table_args__ = (UniqueConstraint("task_id", "idempotency_key", name="uq_maintenance_action_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("maintenance_tasks.id", ondelete="RESTRICT"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    expected_revision: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
