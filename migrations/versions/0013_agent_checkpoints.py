"""增加一键流程 Checkpoint、动作记录和非终态审核暂停。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_agent_checkpoints"
down_revision = "0012_one_click_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("recovery_version", sa.String(32), nullable=True))
    op.add_column("agent_runs", sa.Column("checkpoint_id", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True))
    # 结构定义固定在迁移内，不依赖后续版本的 ORM 字段。
    op.create_table("agent_checkpoints",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("parent_id", sa.String(64)),
        sa.Column("value_type", sa.String(32), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column("metadata_type", sa.String(32), nullable=False),
        sa.Column("metadata_value", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("agent_checkpoint_writes",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), primary_key=True),
        sa.Column("index", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(128), nullable=False),
        sa.Column("value_type", sa.String(32), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False))
    op.create_table("agent_actions",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("action_key", sa.String(64), primary_key=True),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", JSONB(none_as_null=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('started','completed')", name="ck_agent_action_status"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON agent_checkpoints, agent_checkpoint_writes, agent_actions TO machineguard_app")
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.create_check_constraint("ck_job_status", "jobs", "status IN ('queued','running','waiting_review','succeeded','failed','cancelled')")
    op.drop_constraint("ck_job_attempt_status", "job_attempts", type_="check")
    op.create_check_constraint("ck_job_attempt_status", "job_attempts", "status IN ('running','paused','succeeded','failed','cancelled','lease_expired')")


def downgrade():
    # 已使用恢复流程时明确拒绝降级，避免丢失 Checkpoint 或改写暂停历史。
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runs WHERE recovery_version IS NOT NULL) THEN RAISE EXCEPTION '存在恢复流程记录，不能降级'; END IF; END $$")
    op.drop_constraint("ck_job_attempt_status", "job_attempts", type_="check")
    op.create_check_constraint("ck_job_attempt_status", "job_attempts", "status IN ('running','succeeded','failed','cancelled','lease_expired')")
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.create_check_constraint("ck_job_status", "jobs", "status IN ('queued','running','succeeded','failed','cancelled')")
    for name in ("agent_actions", "agent_checkpoint_writes", "agent_checkpoints"):
        op.drop_table(name)
    for column in ("review_started_at", "checkpoint_id", "recovery_version"):
        op.drop_column("agent_runs", column)
