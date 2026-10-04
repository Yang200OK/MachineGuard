"""建立四类身份、可撤销会话和管理审计。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("roles", sa.Column("name", sa.String(32), primary_key=True),
                    sa.CheckConstraint("name IN ('admin','analyst','reviewer','guest')", name="ck_roles_name"))
    op.create_table("users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'", name="ck_users_username"),
        sa.CheckConstraint("(is_guest AND password_hash IS NULL) OR (NOT is_guest AND password_hash IS NOT NULL)", name="ck_users_password"))
    op.create_table("user_roles",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_name", sa.String(32), sa.ForeignKey("roles.name", ondelete="RESTRICT"), primary_key=True))
    op.create_table("sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"))
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table("audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("details", postgresql.JSONB(), nullable=False))
    for name in ("actor_id", "action", "request_id"):
        op.create_index(f"ix_audit_events_{name}", "audit_events", [name])
    op.bulk_insert(sa.table("roles", sa.column("name", sa.String())),
                   [{"name": name} for name in ("admin", "analyst", "reviewer", "guest")])
    # 应用只读角色定义和迁移版本，不能改写权限枚举或伪造迁移状态。
    op.execute("REVOKE INSERT, UPDATE, DELETE ON TABLE roles, alembic_version FROM machineguard_app")


def downgrade():
    # 明确定义反向迁移；降级操作会删除对应结构。
    op.drop_table("audit_events")
    op.drop_table("sessions")
    op.drop_table("user_roles")
    op.drop_table("users")
    op.drop_table("roles")
