"""正式报告导出和模拟维护任务。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_report_delivery"
down_revision = "0014_report_reviews"
branch_labels = depends_on = None


def upgrade():
    # 迁移固定对应版本的结构，不随 ORM 字段变更而改变历史迁移。
    op.create_table("report_exports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("final_reports.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("creator_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("format", sa.String(8), nullable=False), sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("render_config", JSONB(), nullable=False), sa.Column("artifact", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("format IN ('md','json','pdf')", name="ck_export_format"))
    op.create_index("ix_report_exports_report_id", "report_exports", ["report_id"])
    op.create_table("maintenance_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("final_reports.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("creator_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("creation_key", sa.Uuid(), nullable=False), sa.Column("title", sa.String(120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','in_progress','completed','cancelled')", name="ck_maintenance_status"),
        sa.CheckConstraint("revision >= 1", name="ck_maintenance_revision"))
    op.create_table("maintenance_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("maintenance_tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_maintenance_action_key"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON report_exports, maintenance_tasks TO machineguard_app")
    op.execute("REVOKE DELETE, TRUNCATE ON report_exports, maintenance_tasks FROM machineguard_app")
    op.execute("GRANT SELECT, INSERT ON maintenance_actions TO machineguard_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON maintenance_actions FROM machineguard_app")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM report_exports) OR EXISTS (SELECT 1 FROM maintenance_tasks) THEN RAISE EXCEPTION '存在交付记录，不能降级'; END IF; END $$")
    for table in ("maintenance_actions", "maintenance_tasks", "report_exports"):
        op.drop_table(table)
