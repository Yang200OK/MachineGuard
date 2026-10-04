"""版本化设备时序、模型和可信预测；不保存官方测试标签。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class DatasetRecord(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("name", "split", "source_sha256", name="uq_dataset_source"),
                      CheckConstraint("name = 'FD001' AND split = 'test'", name="ck_dataset_online"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(32))
    split: Mapped[str] = mapped_column(String(16))
    source_sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    engine_count: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EngineRecord(Base):
    __tablename__ = "engines"
    __table_args__ = (UniqueConstraint("dataset_id", "engine_id", name="uq_engine_identity"),
                      CheckConstraint("engine_id > 0 AND last_cycle > 0", name="ck_engine_positive"))
    engine_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), index=True)
    engine_id: Mapped[int] = mapped_column(Integer)
    last_cycle: Mapped[int] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean)


class SensorRecord(Base):
    __tablename__ = "sensor_records"
    __table_args__ = (CheckConstraint("cycle > 0", name="ck_sensor_cycle"),)
    engine_key: Mapped[str] = mapped_column(ForeignKey("engines.engine_key", ondelete="RESTRICT"), primary_key=True)
    cycle: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 原始 24 个设置与传感器均为独立数值列，周期保持未标准化。
    for _name in [f"setting_{i}" for i in range(1, 4)] + [f"sensor_{i}" for i in range(1, 22)]:
        locals()[_name] = mapped_column(Float, nullable=False)
    del _name


class ModelVersion(Base):
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    weight_sha256: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("status IN ('published','retired')", name="ck_model_status"),)


class PredictionRecord(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_prediction_request"),
                      CheckConstraint("window_start_cycle > 0 AND end_cycle >= window_start_cycle", name="ck_prediction_window"),
                      CheckConstraint("business_rul >= 0 AND duration_ms >= 0", name="ck_prediction_result"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    engine_key: Mapped[str] = mapped_column(ForeignKey("engines.engine_key", ondelete="RESTRICT"), index=True)
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.version", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[UUID] = mapped_column()
    window_start_cycle: Mapped[int] = mapped_column(Integer)
    end_cycle: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64))
    raw_rul: Mapped[float] = mapped_column(Float)
    business_rul: Mapped[float] = mapped_column(Float)
    postprocessing: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(16))
    risk_rule: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
