"""MCP 独立凭据：摘要、固定能力、签发会话和可撤销状态。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017_mcp_access"
down_revision = "0016_source_graph"
branch_labels = depends_on = None


def upgrade():
    op.create_table("mcp_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("client_name", sa.String(64), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_mcp_credential_key"),
        sa.CheckConstraint("length(client_name) > 0 AND jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) BETWEEN 1 AND 8", name="ck_mcp_credential_scopes"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_mcp_credential_hash"))
    op.create_index("ix_mcp_credentials_owner_id", "mcp_credentials", ["owner_id"])
    op.create_index("ix_mcp_credentials_session_id", "mcp_credentials", ["session_id"])
    op.execute("GRANT SELECT, INSERT ON mcp_credentials TO machineguard_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON mcp_credentials FROM machineguard_app")
    op.execute("GRANT UPDATE (revoked_at) ON mcp_credentials TO machineguard_app")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM mcp_credentials) THEN RAISE EXCEPTION '存在 MCP 凭据记录，不能降级'; END IF; END $$")
    op.drop_table("mcp_credentials")
