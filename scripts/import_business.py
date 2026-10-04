"""事务导入官方测试观测与已验收模型，只复制评估汇总，不导入标签。"""

import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from machineguard.config import load_data_configuration
from machineguard.data.cmapss import RAW_COLUMNS, load_fd001
from machineguard.inference import LSTMPredictor
from machineguard.model_artifacts import file_record, verify_manifest
from machineguard.server.auth_service import audit
from machineguard.server.business_config import load_business_configuration
from machineguard.server.business_models import DatasetRecord, EngineRecord, ModelVersion, SensorRecord
from machineguard.server.database import Database, make_engine
from machineguard.server.settings import ServerSettings


def main():
    settings = ServerSettings()
    database = Database(settings)
    try:
        database.check()
    finally:
        database.engine.dispose()
    config = load_business_configuration()
    data_config = load_data_configuration(ROOT / "configs/data.json")
    dataset = load_fd001(data_config.source_directory, "test")
    predictor = LSTMPredictor(config.bundle_path)
    delivery = verify_manifest(config.bundle_path.parent, "machineguard.lstm_delivery.v1")
    version = predictor.manifest["model_version"]
    evaluation = json.loads((config.bundle_path.parent / "evaluation.json").read_text(encoding="utf-8"))
    training_config = json.loads((config.bundle_path.parent / "training_config.json").read_text(encoding="utf-8"))
    if delivery["model_version"] != version or evaluation["model_version"] != version:
        raise ValueError("离线评估与部署模型版本不一致。")
    model_values = dict(manifest_sha256=file_record(config.bundle_path / "model_manifest.json")["sha256"],
        weight_sha256=next(x["sha256"] for x in predictor.manifest["files"] if x["file"] == "model.pt"),
        configuration={"training": training_config, "completed_epochs": 200,
                       "best_epoch": predictor.manifest["best_epoch"], "window_size": 30,
                       "features": predictor.preprocessing["features"]["feature_columns"], "device": "cpu"},
        metrics={"validation": evaluation["validation"]["raw"],
                 "official_test_terminal": evaluation["official_test_terminal"]["raw"],
                 "evaluation_sha256": file_record(config.bundle_path.parent / "evaluation.json")["sha256"]},
        status="published")
    # 只有本地管理入口使用迁移账号写证据表，线上账号不具备该权限。
    engine = make_engine(settings.migration_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(774413002)"))
            source = session.scalar(select(DatasetRecord).where(DatasetRecord.name == "FD001",
                DatasetRecord.split == "test", DatasetRecord.source_sha256 == dataset.source_sha256))
            reused = source is not None
            if source is None:
                source = DatasetRecord(id=uuid4(), name="FD001", split="test", source_sha256=dataset.source_sha256,
                    row_count=len(dataset.records), engine_count=int(dataset.records.engine_id.nunique()),
                    provenance={"source": "NASA C-MAPSS FD001 模拟数据", "filename": "test_FD001.txt",
                                "source_size_bytes": dataset.source_size_bytes, "rul_unit": "运行周期",
                                "demo_engine_ids": config.demo_engine_ids, "labels_imported": False})
                session.add(source)
                session.flush()
                for number, group in dataset.records.groupby("engine_id", sort=True):
                    key = f"FD001-test-{source.id.hex}-{int(number):03d}"
                    session.add(EngineRecord(engine_key=key, dataset_id=source.id, engine_id=int(number),
                        last_cycle=int(group.cycle.max()), is_demo=int(number) in config.demo_engine_ids))
                    session.flush()
                    rows = group.drop(columns="engine_id").to_dict(orient="records")
                    session.execute(insert(SensorRecord), [{"engine_key": key, **row} for row in rows])
                audit(session, None, "dataset.imported", "dataset", source.id, None,
                      {"source_sha256": source.source_sha256, "row_count": source.row_count})
            # 重复导入也逐项核对原始值和示例策略，拒绝修补或悄悄覆盖已有数据版本。
            devices = session.scalars(select(EngineRecord).where(EngineRecord.dataset_id == source.id)
                                      .order_by(EngineRecord.engine_id)).all()
            if source.row_count != len(dataset.records) or source.engine_count != len(devices) or len(devices) != 100:
                raise ValueError("已登记数据规模不一致。")
            for device in devices:
                expected = dataset.records.loc[dataset.records.engine_id == device.engine_id, list(RAW_COLUMNS[1:])]
                rows = session.scalars(select(SensorRecord).where(SensorRecord.engine_key == device.engine_key)
                                       .order_by(SensorRecord.cycle)).all()
                actual = np.asarray([[getattr(row, name) for name in RAW_COLUMNS[1:]] for row in rows], dtype=np.float64)
                if (not np.array_equal(actual, expected.to_numpy(dtype=np.float64)) or
                        device.last_cycle != int(expected.cycle.max()) or
                        device.is_demo != (device.engine_id in config.demo_engine_ids)):
                    raise ValueError(f"设备 {device.engine_id} 的登记值与原始文件或示例配置不一致。")
            model = session.get(ModelVersion, version)
            if model is None:
                session.add(ModelVersion(version=version, **model_values))
                audit(session, None, "model.registered", "model", None, None, {"model_version": version})
            elif any(getattr(model, name) != value for name, value in model_values.items()):
                raise ValueError("同版本模型登记内容不一致，禁止覆盖。")
            print(f"数据：FD001/test；设备 {len(devices)}；原始行 {source.row_count}；示例设备 {len(config.demo_engine_ids)}")
            print(f"数据版本：{source.source_sha256}")
            print(f"数据状态：{'重复导入核对通过，未新增记录' if reused else '新版本逐值核对通过'}")
        print(f"事务已提交；模型 {version}，200 轮、best 第 {predictor.manifest['best_epoch']} 轮；未导入官方测试标签。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
