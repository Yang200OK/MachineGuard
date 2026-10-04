"""文档版本管理与审核；原文、来源快照和解析正文保持不变。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_knowledge_review"
down_revision = "0005_document_parsing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_versions", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("document_versions", sa.Column("management_revision", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_document_management_revision", "document_versions", "management_revision >= 0")
    op.create_table("document_metadata_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False), sa.Column("source_metadata", JSONB(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("version_id", "number", name="uq_document_metadata_revision"),
        sa.CheckConstraint("number > 0", name="ck_metadata_revision_number"))
    op.create_index("ix_document_metadata_revisions_version_id", "document_metadata_revisions", ["version_id"])
    op.create_table("document_management_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("parse_id", sa.Uuid(), sa.ForeignKey("document_parses.id", ondelete="RESTRICT")),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False), sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("decision", sa.String(16)),
        sa.Column("metadata_revision", sa.Integer(), nullable=False), sa.Column("result_sha256", sa.String(64)),
        sa.Column("payload", JSONB(), nullable=False), sa.Column("result", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_document_management_key"),
        sa.CheckConstraint("kind IN ('metadata','deactivate','review')", name="ck_document_management_kind"),
        sa.CheckConstraint("metadata_revision >= 0 AND ((kind = 'review' AND parse_id IS NOT NULL AND decision IS NOT NULL AND decision IN ('approved','rejected') AND result_sha256 IS NOT NULL) OR (kind <> 'review' AND parse_id IS NULL AND decision IS NULL AND result_sha256 IS NULL))", name="ck_document_management_review"))
    op.create_index("ix_document_management_actions_version_id", "document_management_actions", ["version_id"])
    op.create_index("ix_document_management_actions_parse_id", "document_management_actions", ["parse_id"])


def downgrade():
    op.drop_table("document_management_actions")
    op.drop_table("document_metadata_revisions")
    op.drop_constraint("ck_document_management_revision", "document_versions", type_="check")
    op.drop_column("document_versions", "management_revision")
    op.drop_column("document_versions", "is_active")
