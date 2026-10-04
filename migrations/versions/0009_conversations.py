"""增加本人会话和冻结的轮次上下文，原单轮记录保持原状。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_conversations"
down_revision = "0008_knowledge_answers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("knowledge_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False), sa.Column("equipment_type", sa.String(200)),
        sa.Column("device_context", JSONB()), sa.Column("config_snapshot", JSONB(), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False), sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key"))
    op.create_index("ix_knowledge_conversations_owner_id", "knowledge_conversations", ["owner_id"])
    op.add_column("knowledge_answers", sa.Column("conversation_id", sa.Uuid(),
        sa.ForeignKey("knowledge_conversations.id", ondelete="RESTRICT")))
    op.add_column("knowledge_answers", sa.Column("turn_number", sa.Integer()))
    op.add_column("knowledge_answers", sa.Column("context_snapshot", JSONB()))
    op.create_index("ix_knowledge_answers_conversation_id", "knowledge_answers", ["conversation_id"])
    op.create_unique_constraint("uq_answer_conversation_turn", "knowledge_answers", ["conversation_id", "turn_number"])


def downgrade():
    op.drop_constraint("uq_answer_conversation_turn", "knowledge_answers", type_="unique")
    op.drop_index("ix_knowledge_answers_conversation_id", "knowledge_answers")
    for name in ("context_snapshot", "turn_number", "conversation_id"):
        op.drop_column("knowledge_answers", name)
    op.drop_table("knowledge_conversations")
