"""新增单轮问答记录，任务与可信预测的既有表保持原状。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_knowledge_answers"
down_revision = "0007_knowledge_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("knowledge_answers", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False), sa.Column("equipment_type", sa.String(200)),
        sa.Column("config_snapshot", JSONB(), nullable=False), sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("trace", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result", JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_knowledge_answers_owner_id", "knowledge_answers", ["owner_id"])


def downgrade():
    op.drop_table("knowledge_answers")
