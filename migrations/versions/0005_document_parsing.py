"""新增解析、父页面、切片与请求键，不改变既有上传和任务表。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_document_parsing"
down_revision = "0004_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("document_parses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True, nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("config_snapshot", JSONB(), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_metadata", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result_sha256", sa.String(64)),
        sa.Column("page_count", sa.Integer()), sa.Column("chunk_count", sa.Integer()),
        sa.CheckConstraint("mode IN ('native','vision')", name="ck_parse_mode"),
        sa.CheckConstraint("(completed_at IS NULL AND result_sha256 IS NULL AND page_count IS NULL AND chunk_count IS NULL) OR (completed_at IS NOT NULL AND result_sha256 IS NOT NULL AND page_count IS NOT NULL AND chunk_count IS NOT NULL AND page_count > 0 AND chunk_count > 0)", name="ck_parse_result"))
    op.create_index("ix_document_parses_version_id", "document_parses", ["version_id"])
    op.create_table("document_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parse_id", sa.Uuid(), sa.ForeignKey("document_parses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False), sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("extraction", JSONB(), nullable=False),
        sa.UniqueConstraint("parse_id", "number", name="uq_parse_page"),
        sa.CheckConstraint("number > 0 AND char_start >= 0 AND char_end >= char_start", name="ck_parse_page_limits"))
    op.create_index("ix_document_pages_parse_id", "document_pages", ["parse_id"])
    op.create_table("chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parse_id", sa.Uuid(), sa.ForeignKey("document_parses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("document_version", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False), sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False), sa.Column("page_spans", JSONB(), nullable=False),
        sa.Column("section", sa.String(200), nullable=False), sa.Column("language", sa.String(8), nullable=False),
        sa.Column("source_metadata", JSONB(), nullable=False),
        sa.UniqueConstraint("parse_id", "ordinal", name="uq_parse_chunk"),
        sa.CheckConstraint("ordinal > 0 AND token_count > 0 AND token_count <= 700 AND char_start >= 0 AND char_end > char_start AND page > 0", name="ck_parse_chunk_limits"))
    op.create_index("ix_chunks_parse_id", "chunks", ["parse_id"])
    op.create_index("ix_chunks_document_version", "chunks", ["document_version"])
    op.create_table("document_parse_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("parse_id", sa.Uuid(), sa.ForeignKey("document_parses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_parse_request"))


def downgrade():
    op.drop_table("document_parse_requests")
    op.drop_table("chunks")
    op.drop_table("document_pages")
    op.drop_table("document_parses")
