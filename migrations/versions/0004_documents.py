"""新增文档原文与上传请求记录，不改变已有账号、设备和任务。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_documents"
down_revision = "0003_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("creation_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("created_by", "creation_fingerprint", name="uq_document_creation"))
    op.create_table("document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_metadata", JSONB(), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("file_format", sa.String(8), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("oss_bucket", sa.String(63), nullable=False),
        sa.Column("oss_region", sa.String(40), nullable=False),
        sa.Column("object_key", sa.String(256), unique=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("upload_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("etag", sa.String(128)),
        sa.UniqueConstraint("document_id", "number", name="uq_document_version_number"),
        sa.UniqueConstraint("document_id", "fingerprint", name="uq_document_version_fingerprint"),
        sa.CheckConstraint("number > 0 AND attempt_count >= 0 AND size_bytes > 0 AND size_bytes <= 10485760", name="ck_document_version_limits"),
        sa.CheckConstraint("status IN ('uploading','uploaded','failed')", name="ck_document_version_status"),
        sa.CheckConstraint("file_format IN ('pdf','md','txt')", name="ck_document_version_format"),
        sa.CheckConstraint("(status = 'uploading' AND upload_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'uploading' AND upload_token IS NULL AND lease_expires_at IS NULL)", name="ck_document_version_lease"),
        sa.CheckConstraint("(status = 'uploaded' AND uploaded_at IS NOT NULL AND etag IS NOT NULL AND error_code IS NULL) OR (status <> 'uploaded' AND uploaded_at IS NULL AND etag IS NULL)", name="ck_document_version_result"))
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_table("document_upload_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("target_document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="RESTRICT")),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_document_upload_key"))


def downgrade():
    op.drop_table("document_upload_requests")
    op.drop_table("document_versions")
    op.drop_table("documents")
