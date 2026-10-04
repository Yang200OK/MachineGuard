"""分析运行、固定范围和工具/模型轨迹；不改变已有知识与预测表。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0011_agent_runs"
down_revision = "0010_workspace_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        *[sa.Column(name, pg.JSONB(), nullable=False) for name in ("input_scope", "config_snapshot", "skill_catalog")],
        *[sa.Column(name, pg.JSONB(), nullable=True) for name in ("selected_skill", "tool_scope", "result")],
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in ("model_calls", "tool_calls", "event_sequence")],
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("model_calls BETWEEN 0 AND 6 AND tool_calls BETWEEN 0 AND 8 AND event_sequence >= 0", name="ck_agent_budgets"))
    op.create_index("ix_agent_runs_owner_id", "agent_runs", ["owner_id"])
    op.create_table("agent_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("details", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_event_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_agent_event_positive"))
    op.create_index("ix_agent_events_run_id", "agent_events", ["run_id"])
    # 沿用现有运行账号角色，不授予迁移或其它数据库权限。
    op.execute("GRANT SELECT, INSERT, UPDATE ON agent_runs, agent_events TO machineguard_app")


def downgrade():
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
