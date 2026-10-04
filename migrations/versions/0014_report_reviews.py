"""人工审核、分配和正式报告。

Revision ID: 0014_report_reviews
Revises: 0013_agent_checkpoints
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_report_reviews"
down_revision = "0013_agent_checkpoints"
branch_labels = depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("assigned_reviewer_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")))
    op.create_table("report_reviews",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("reviewer_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False), sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("checkpoint_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("decision IN ('approved','rejected')", name="ck_report_review_decision"))
    op.create_table("final_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("report_reviews.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("prediction_id", sa.Uuid(), sa.ForeignKey("predictions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content", JSONB(), nullable=False), sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_final_reports_owner_id", "final_reports", ["owner_id"])
    # 决定和正式报告只允许插入与读取，运行账号无更新、删除权限。
    op.execute("GRANT SELECT, INSERT ON report_reviews, final_reports TO machineguard_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON report_reviews, final_reports FROM machineguard_app")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM report_reviews) THEN RAISE EXCEPTION '存在人工审核记录，不能降级'; END IF; END $$")
    op.drop_table("final_reports")
    op.drop_table("report_reviews")
    op.drop_column("agent_runs", "assigned_reviewer_id")
