"""冻结一键分析参数，报告候选随已校验分析结果原子保存。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0012_one_click_reports"
down_revision = "0011_agent_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("workflow", sa.String(20), nullable=False, server_default="agent"))
    op.add_column("agent_runs", sa.Column("workflow_options", pg.JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint("ck_agent_workflow", "agent_runs", "(workflow = 'agent' AND workflow_options IS NULL) OR (workflow = 'one_click' AND workflow_options IS NOT NULL)")


def downgrade():
    op.drop_constraint("ck_agent_workflow", "agent_runs", type_="check")
    op.drop_column("agent_runs", "workflow_options")
    op.drop_column("agent_runs", "workflow")
