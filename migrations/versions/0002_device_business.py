"""新增设备业务表，不改写已有身份数据。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_device_business"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("split", sa.String(16), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("engine_count", sa.Integer(), nullable=False),
        sa.Column("provenance", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", "split", "source_sha256", name="uq_dataset_source"),
        sa.CheckConstraint("name = 'FD001' AND split = 'test'", name="ck_dataset_online"))
    op.create_table("engines",
        sa.Column("engine_key", sa.String(96), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("engine_id", sa.Integer(), nullable=False),
        sa.Column("last_cycle", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("dataset_id", "engine_id", name="uq_engine_identity"),
        sa.CheckConstraint("engine_id > 0 AND last_cycle > 0", name="ck_engine_positive"))
    op.create_index("ix_engines_dataset_id", "engines", ["dataset_id"])
    op.create_table("sensor_records",
        sa.Column("engine_key", sa.String(96), sa.ForeignKey("engines.engine_key", ondelete="RESTRICT"), primary_key=True),
        sa.Column("cycle", sa.Integer(), primary_key=True),
        *[sa.Column(name, sa.Float(), nullable=False) for name in
          [f"setting_{i}" for i in range(1, 4)] + [f"sensor_{i}" for i in range(1, 22)]],
        sa.CheckConstraint("cycle > 0", name="ck_sensor_cycle"))
    op.create_table("model_versions",
        sa.Column("version", sa.String(64), primary_key=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("weight_sha256", sa.String(64), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('published','retired')", name="ck_model_status"))
    op.create_table("predictions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("engine_key", sa.String(96), sa.ForeignKey("engines.engine_key", ondelete="RESTRICT"), nullable=False),
        sa.Column("model_version", sa.String(64), sa.ForeignKey("model_versions.version", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("window_start_cycle", sa.Integer(), nullable=False),
        sa.Column("end_cycle", sa.Integer(), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("raw_rul", sa.Float(), nullable=False),
        sa.Column("business_rul", sa.Float(), nullable=False),
        sa.Column("postprocessing", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("risk_rule", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_prediction_request"),
        sa.CheckConstraint("window_start_cycle > 0 AND end_cycle >= window_start_cycle", name="ck_prediction_window"),
        sa.CheckConstraint("business_rul >= 0 AND duration_ms >= 0", name="ck_prediction_result"))
    for name in ("owner_id", "engine_key", "created_at"):
        op.create_index(f"ix_predictions_{name}", "predictions", [name])
    # 线上运行账号只读已导入的时序和模型，避免在线工具改写证据。
    op.execute("REVOKE INSERT, UPDATE, DELETE ON datasets, engines, sensor_records, model_versions FROM machineguard_app")


def downgrade():
    for table in ("predictions", "model_versions", "sensor_records", "engines", "datasets"):
        op.drop_table(table)
