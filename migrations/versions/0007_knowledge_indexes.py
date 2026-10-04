"""先安装 pgvector 扩展，再建立知识快照与精确向量索引数据。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = "0007_knowledge_indexes"
down_revision = "0006_knowledge_review"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().scalar(sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")) is None:
        raise RuntimeError("请先安装并由数据库管理员启用 pgvector；迁移账号不提升权限。")
    op.create_table("knowledge_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("base_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT")),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("config_snapshot", JSONB(), nullable=False), sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("members", JSONB(), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("chunk_count", sa.Integer()), sa.Column("usage", JSONB()),
        sa.CheckConstraint("(published_at IS NULL AND manifest_sha256 IS NULL AND chunk_count IS NULL) OR (published_at IS NOT NULL AND manifest_sha256 IS NOT NULL AND chunk_count > 0)", name="ck_snapshot_publication"))
    op.create_index("ix_knowledge_snapshots_fingerprint", "knowledge_snapshots", ["fingerprint"])
    op.create_table("knowledge_catalog", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("current_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1 AND revision >= 0", name="ck_catalog_singleton"))
    op.execute("INSERT INTO knowledge_catalog(id) VALUES (1)")
    op.create_table("knowledge_publish_requests", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_knowledge_publish_key"))
    op.create_table("snapshot_chunks",
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("chunks.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_metadata", JSONB(), nullable=False), sa.Column("location", JSONB(), nullable=False),
        sa.Column("tokens", JSONB(), nullable=False), sa.Column("embedding", Vector(1024), nullable=False))
    op.create_index("ix_snapshot_chunks_version_id", "snapshot_chunks", ["version_id"])


def downgrade():
    op.drop_table("snapshot_chunks")
    op.drop_table("knowledge_publish_requests")
    op.drop_table("knowledge_catalog")
    op.drop_table("knowledge_snapshots")
