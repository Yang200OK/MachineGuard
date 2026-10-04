"""API、Agent 与 MCP 共用的设备授权、趋势和预测业务。"""

from hashlib import sha256
import json
import math
from threading import Lock

import numpy as np
import pandas as pd
from sqlalchemy import func, select, text

from machineguard.data.cmapss import DatasetError, RAW_COLUMNS
from machineguard.inference import LSTMPredictor
from machineguard.model_artifacts import file_record
from .auth_service import ServiceError, audit
from .business_models import DatasetRecord, EngineRecord, ModelVersion, PredictionRecord, SensorRecord


def pagination(offset, limit):
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise ServiceError(422, "PAGINATION_INVALID", "分页参数超出范围。")


def engine_view(engine, dataset):
    return {"engine_key": engine.engine_key, "dataset_id": str(dataset.id), "dataset": dataset.name,
            "split": dataset.split, "data_version": dataset.source_sha256,
            "engine_id": engine.engine_id, "last_observed_cycle": engine.last_cycle,
            "is_demo": engine.is_demo, "source": "NASA C-MAPSS FD001 模拟数据", "rul_unit": "运行周期"}


def authorized_engine(session, principal, engine_key):
    principal.require("devices:read")
    row = session.execute(select(EngineRecord, DatasetRecord).join(DatasetRecord).where(
        EngineRecord.engine_key == engine_key)).first()
    if row is None:
        raise ServiceError(404, "ENGINE_NOT_FOUND", "设备不存在。")
    engine, dataset = row
    if principal.user.is_guest and not engine.is_demo:
        raise ServiceError(403, "ENGINE_FORBIDDEN", "访客仅可访问示例设备。")
    return engine, dataset


def list_engines(session, principal, offset=0, limit=20, dataset_id=None):
    principal.require("devices:read")
    pagination(offset, limit)
    query = select(EngineRecord, DatasetRecord).join(DatasetRecord)
    if principal.user.is_guest:
        query = query.where(EngineRecord.is_demo.is_(True))
    if dataset_id is not None:
        query = query.where(EngineRecord.dataset_id == dataset_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.execute(query.order_by(DatasetRecord.created_at, DatasetRecord.id, EngineRecord.engine_id)
                           .offset(offset).limit(limit)).all()
    return {"items": [engine_view(e, d) for e, d in rows], "total": total, "offset": offset, "limit": limit}


def bounded_frame(session, engine, end_cycle, start_cycle):
    if type(end_cycle) is not int or not 1 <= end_cycle <= engine.last_cycle:
        raise ServiceError(422, "CYCLE_INVALID", "选择周期超出该设备的观测范围。")
    if type(start_cycle) is not int or not 1 <= start_cycle <= end_cycle:
        raise ServiceError(422, "CYCLE_INVALID", "起始周期必须为正整数且不大于末端周期。")
    rows = session.scalars(select(SensorRecord).where(SensorRecord.engine_key == engine.engine_key,
        SensorRecord.cycle >= start_cycle, SensorRecord.cycle <= end_cycle).order_by(SensorRecord.cycle)).all()
    if len(rows) != end_cycle - start_cycle + 1 or any(r.cycle != start_cycle + i for i, r in enumerate(rows)):
        raise ServiceError(422, "WINDOW_DISCONTINUOUS", "观测周期缺失或断裂。")
    frame = pd.DataFrame([{name: engine.engine_id if name == "engine_id" else getattr(row, name)
                           for name in RAW_COLUMNS} for row in rows], columns=RAW_COLUMNS)
    if not np.isfinite(frame.to_numpy(dtype=np.float64)).all():
        raise ServiceError(422, "WINDOW_NONFINITE", "原始观测存在非有限数值。")
    return frame


def sensor_trends(session, principal, engine_key, end_cycle, start_cycle=1, columns=None):
    engine, dataset = authorized_engine(session, principal, engine_key)
    columns = ["sensor_2", "sensor_3", "sensor_4"] if columns is None else columns
    if not columns or len(columns) > 24 or len(set(columns)) != len(columns) or any(x not in RAW_COLUMNS[2:] for x in columns):
        raise ServiceError(422, "SENSOR_INVALID", "传感器必须为唯一的原始字段名，不接受标签或未知字段。")
    frame = bounded_frame(session, engine, end_cycle, start_cycle)
    cycles = frame.cycle.to_numpy(dtype=np.float64)
    trends = []
    for column in columns:
        values = frame[column].to_numpy(dtype=np.float64)
        first, last = float(values[0]), float(values[-1])
        slope = float(np.dot(cycles - cycles.mean(), values - values.mean()) / np.dot(cycles - cycles.mean(), cycles - cycles.mean())) if len(cycles) > 1 else None
        trends.append({"field": column, "unit": "未知", "mean": float(values.mean()),
                       "first": first, "last": last, "change": last - first,
                       "relative_change": (last - first) / abs(first) if first != 0 else None,
                       "relative_change_status": "defined" if first != 0 else "zero_denominator",
                       "slope_per_cycle": slope,
                       "observation": "升高" if last > first else "降低" if last < first else "不变"})
    return {"engine": engine_view(engine, dataset), "start_cycle": start_cycle, "end_cycle": end_cycle,
            "records": frame[["cycle", *columns]].to_dict(orient="records"), "trends": trends,
            "method": "均值；末值减首值；变化量/首值绝对值；对原始周期的最小二乘斜率。",
            "limitation": "数值变化是观测，不足以证明部件故障；未知物理单位不补写。"}


def prediction_view(row):
    return {"prediction_id": str(row.id), "engine_key": row.engine_key,
            "model_version": row.model_version, "window_start_cycle": row.window_start_cycle,
            "end_cycle": row.end_cycle, "window_size": row.end_cycle - row.window_start_cycle + 1,
            "input_sha256": row.input_sha256, "raw_rul": row.raw_rul, "business_rul": row.business_rul,
            "postprocessing": row.postprocessing, "duration_ms": row.duration_ms, "device": "cpu",
            "rul_unit": "运行周期", "risk_level": row.risk_level, "risk_rule": row.risk_rule,
            "request_id": str(row.request_id), "created_at": row.created_at.isoformat()}


def prediction_history(session, principal, engine_key, end_cycle, offset=0, limit=20):
    engine, _ = authorized_engine(session, principal, engine_key)
    pagination(offset, limit)
    if type(end_cycle) is not int or not 1 <= end_cycle <= engine.last_cycle:
        raise ServiceError(422, "CYCLE_INVALID", "历史查询需要有效的当前周期。")
    # 所有角色均只读取本人历史，且不泄漏选定周期之后的预测。
    query = select(PredictionRecord).where(PredictionRecord.owner_id == principal.user.id,
        PredictionRecord.engine_key == engine_key, PredictionRecord.end_cycle <= end_cycle)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(PredictionRecord.created_at.desc(), PredictionRecord.id.desc())
                           .offset(offset).limit(limit)).all()
    return {"items": [prediction_view(row) for row in rows], "total": total, "offset": offset, "limit": limit, "end_cycle": end_cycle}


def list_models(session, principal):
    principal.require("devices:read")
    rows = session.scalars(select(ModelVersion).where(ModelVersion.status == "published").order_by(ModelVersion.version)).all()
    return {"items": [{"model_version": r.version, "name": "LSTM", "status": r.status,
            "manifest_sha256": r.manifest_sha256, "weight_sha256": r.weight_sha256,
            "configuration": r.configuration, "metrics": r.metrics} for r in rows]}


class DeviceRuntime:
    def __init__(self, config):
        self.config = config
        self.predictor = LSTMPredictor(config.bundle_path)
        self.manifest_sha256 = file_record(config.bundle_path / "model_manifest.json")["sha256"]
        self.lock = Lock()

    def check_registration(self, session):
        row = session.get(ModelVersion, self.predictor.manifest["model_version"])
        weight_hash = next(x["sha256"] for x in self.predictor.manifest["files"] if x["file"] == "model.pt")
        if row is None or row.status != "published" or row.manifest_sha256 != self.manifest_sha256 or row.weight_sha256 != weight_hash:
            raise ServiceError(503, "MODEL_UNAVAILABLE", "部署模型未登记、未发布或哈希不一致。")
        rules = session.scalars(select(PredictionRecord.risk_rule).where(
            PredictionRecord.risk_rule["version"].astext == self.config.risk_version).distinct()).all()
        if any(rule != self.config.risk_rule for rule in rules):
            raise ServiceError(503, "RISK_RULE_CONFLICT", "同版本风险规则已经使用不同阈值，请升级规则版本。")
        return row

    def predict(self, session, principal, payload, request_id):
        principal.require("predictions:create")
        engine, _ = authorized_engine(session, principal, payload.engine_key)
        version = payload.model_version or self.predictor.manifest["model_version"]
        canonical = json.dumps({"engine_key": payload.engine_key, "end_cycle": payload.end_cycle,
                               "model_version": version}, sort_keys=True, separators=(",", ":"))
        fingerprint = sha256(canonical.encode("utf-8")).hexdigest()
        # 事务锁串行化同一用户请求键，提交后释放；唯一约束同时兜住写入竞争。
        lock_id = int.from_bytes(sha256(f"prediction:{principal.user.id}:{payload.idempotency_key}".encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        existing = session.scalar(select(PredictionRecord).where(PredictionRecord.owner_id == principal.user.id,
                                  PredictionRecord.idempotency_key == payload.idempotency_key))
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "该请求键已用于另一组预测参数。")
            return prediction_view(existing), True
        if version != self.predictor.manifest["model_version"]:
            raise ServiceError(422, "MODEL_VERSION_INVALID", "请求模型版本未装载，不自动切换模型。")
        self.check_registration(session)
        size = self.predictor.preprocessing["configuration"]["window_size"]
        if payload.end_cycle < size:
            raise ServiceError(422, "WINDOW_INSUFFICIENT", f"预测需要至少 {size} 个连续周期。")
        frame = bounded_frame(session, engine, payload.end_cycle, payload.end_cycle - size + 1)
        try:
            # 同一 CPU 模型实例串行推理，业务事务分别保存所属用户和请求身份。
            with self.lock:
                result = self.predictor.predict(frame, engine.engine_id, payload.end_cycle)
        except DatasetError as error:
            raise ServiceError(422, "WINDOW_INVALID", str(error)) from error
        rule = self.config.risk_rule
        level = "critical" if result["business_rul"] <= rule["critical_max"] else "warning" if result["business_rul"] <= rule["warning_max"] else "normal"
        if not all(math.isfinite(result[k]) for k in ("raw_rul", "business_rul", "duration_ms")):
            raise ServiceError(500, "PREDICTION_NONFINITE", "模型返回非有限数值。")
        row = PredictionRecord(owner_id=principal.user.id, engine_key=engine.engine_key, model_version=version,
            idempotency_key=payload.idempotency_key, request_fingerprint=fingerprint, request_id=request_id,
            window_start_cycle=result["window_start_cycle"], end_cycle=payload.end_cycle,
            input_sha256=result["input_sha256"], raw_rul=result["raw_rul"], business_rul=result["business_rul"],
            postprocessing=result["postprocessing"], duration_ms=result["duration_ms"], risk_level=level, risk_rule=rule)
        session.add(row)
        session.flush()
        audit(session, principal.user.id, "prediction.created", "prediction", row.id, request_id,
              {"engine_key": engine.engine_key, "end_cycle": payload.end_cycle, "model_version": version})
        return prediction_view(row), False
