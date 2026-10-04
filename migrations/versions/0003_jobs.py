"""新增任务队列与执行记录，保留已有账号、设备和预测。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_jobs"
down_revision = "0002_device_business"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_job_request"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="ck_job_status"),
        sa.CheckConstraint("attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempt_count <= max_attempts", name="ck_job_attempts"),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_job_progress"),
        sa.CheckConstraint("deadline_at > created_at", name="ck_job_deadline"),
        sa.CheckConstraint("(status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND worker_id IS NOT NULL)", name="ck_job_lease"),
        sa.CheckConstraint("(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)", name="ck_job_finished"))
    for name in ("owner_id", "status", "created_at"):
        op.create_index(f"ix_jobs_{name}", "jobs", [name])
    op.create_table("job_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(256)),
        sa.UniqueConstraint("job_id", "number", name="uq_job_attempt_number"),
        sa.CheckConstraint("number > 0", name="ck_job_attempt_positive"),
        sa.CheckConstraint("status IN ('running','succeeded','failed','cancelled','lease_expired')", name="ck_job_attempt_status"))
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_index("ix_jobs_queue", "jobs", ["status", "created_at", "id"])
    op.create_index("ix_jobs_lease", "jobs", ["status", "lease_expires_at"])


def downgrade():
    op.drop_table("job_attempts")
    op.drop_table("jobs")
