"""目录分类与可恢复删除；保留任务、问答和原文引用关系。"""
from alembic import op
import sqlalchemy as sa

revision = "0010_workspace_records"
down_revision = "0009_conversations"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("jobs", "knowledge_conversations"):
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True)))
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])
    op.add_column("documents", sa.Column("category", sa.String(40), server_default="知识助手与管理", nullable=False))
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE documents SET category = '验收资料' WHERE title LIKE '%验收%'")


def downgrade():
    for table in ("jobs", "knowledge_conversations"):
        op.drop_index(f"ix_{table}_deleted_at", table)
        op.drop_column(table, "deleted_at")
    op.drop_column("documents", "archived_at")
    op.drop_column("documents", "category")
