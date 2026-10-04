"""只接收当前设备连续原始窗口的 CPU Predictor，不读取真实标签。"""

from hashlib import sha256
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import torch

from .checkpoints import load_model_checkpoint
from .data.cmapss import DatasetError, RAW_COLUMNS
from .data.preparation import CANDIDATE_FEATURE_COLUMNS
from .model_artifacts import verify_manifest
from .training import TrainingError


def standardize_window(records: pd.DataFrame, preprocessing: dict,
                       engine_id: int, end_cycle: int) -> tuple[torch.Tensor, str]:
    """要求恰好一个设备的连续窗口，原始周期排序后末端必须匹配。"""
    for name, value in (("engine_id", engine_id), ("end_cycle", end_cycle)):
        if type(value) is not int or not 0 < value <= np.iinfo(np.int64).max:
            raise DatasetError(f"{name} 必须是有效正整数。")
    window_size = preprocessing["configuration"]["window_size"]
    if not isinstance(records, pd.DataFrame) or len(records) != window_size:
        raise DatasetError(f"预测必须提供恰好 {window_size} 个原始周期，不补零。")
    if len(records.columns) != len(RAW_COLUMNS) or set(records.columns) != set(RAW_COLUMNS):
        raise DatasetError("预测必须提供全部 26 个原始字段；不接收标签或附加字段。")
    frame = records.loc[:, list(RAW_COLUMNS)].copy(deep=True)
    for column in RAW_COLUMNS:
        if not pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]):
            raise DatasetError(f"{column} 必须为数值，不能为布尔值或字符串。")
    values = frame.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise DatasetError("预测窗口存在 NaN/Inf。")
    for column in RAW_COLUMNS[:2]:
        integers = frame[column].to_numpy()
        if any(value <= 0 or value > np.iinfo(np.int64).max or value != int(value) for value in integers.tolist()):
            raise DatasetError(f"预测窗口的 {column} 必须为有效正整数。")
        frame[column] = frame[column].astype(np.int64)
    if not frame["engine_id"].eq(engine_id).all():
        raise DatasetError("预测窗口混入其它设备或设备编号不匹配。")
    frame = frame.sort_values("cycle", ignore_index=True)
    cycles = frame["cycle"].to_numpy(dtype=np.int64)
    if np.any(np.diff(cycles) != 1):
        raise DatasetError("预测窗口周期断裂或重复。")
    if int(cycles[-1]) != end_cycle or int(cycles[0]) != end_cycle - window_size + 1:
        raise DatasetError("预测窗口必须以请求周期为末端，不能使用未来周期。")
    columns = preprocessing["features"]["feature_columns"]
    if (len(columns) != 18 or columns != [name for name in CANDIDATE_FEATURE_COLUMNS
                                         if name not in preprocessing["features"]["constant_columns"]]):
        raise TrainingError("模型有序特征非法。")
    scaler = preprocessing["scaler"]
    mean, scale = np.asarray(scaler["mean"], dtype=np.float64), np.asarray(scaler["scale"], dtype=np.float64)
    if mean.shape != (18,) or scale.shape != (18,) or not np.isfinite(mean).all() or not np.isfinite(scale).all() or np.any(scale <= 0):
        raise TrainingError("模型标准化参数非法。")
    scaled = ((frame[columns].to_numpy(dtype=np.float64) - mean) / scale).astype(np.float32)
    if not np.isfinite(scaled).all():
        raise DatasetError("标准化窗口转 FP32 后存在 NaN/Inf。")
    # 原始字段和顺序规范化后哈希；不把真实 RUL 或未来数据装入请求。
    encoded = json.dumps(frame.to_dict(orient="records"), ensure_ascii=False,
                         allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return torch.from_numpy(scaled).unsqueeze(0), sha256(encoded).hexdigest()


class LSTMPredictor:
    """启动一次加载部署包，预测仅返回可信数值与窗口身份。"""

    def __init__(self, bundle_directory: Path):
        bundle_directory = Path(bundle_directory).resolve()
        self.manifest = verify_manifest(bundle_directory, "machineguard.lstm_deployment.v1")
        if self.manifest["completed_epochs"] != 200 or self.manifest["training_mode"] != "training":
            raise TrainingError("部署模型必须来自完整的 200 轮正式训练。")
        if {item["file"] for item in self.manifest["files"]} != {"model.pt", "preprocessing.json"}:
            raise TrainingError("部署清单只能包含权重与预处理参数。")
        self.model, payload = load_model_checkpoint(bundle_directory / "model.pt")
        self.preprocessing = json.loads((bundle_directory / "preprocessing.json").read_text(encoding="utf-8"))
        if (payload["mode"] != "training" or payload["kind"] != "best" or payload["target_epochs"] != 200
                or payload["run_id"] != self.manifest["model_version"] or payload["epoch"] != self.manifest["best_epoch"]
                or payload["preprocessing"] != self.preprocessing):
            raise TrainingError("部署权重、版本与预处理文件不一致。")
        if any(not torch.isfinite(value).all() for value in self.model.state_dict().values()):
            raise TrainingError("模型权重包含非有限数值。")

    def predict(self, records: pd.DataFrame, engine_id: int, end_cycle: int) -> dict:
        """原始 RUL 与 max(0, raw_rul) 分别保存，单位为运行周期。"""
        started = time.perf_counter()
        inputs, input_hash = standardize_window(records, self.preprocessing, engine_id, end_cycle)
        with torch.inference_mode():
            predictions = self.model(inputs)
        if predictions.shape != (1,) or not torch.isfinite(predictions).all():
            raise TrainingError("CPU 模型预测形状或数值非法。")
        raw_rul = float(predictions[0])
        return {"dataset": "FD001", "engine_id": engine_id, "end_cycle": end_cycle,
                "window_start_cycle": end_cycle - inputs.shape[1] + 1, "window_size": inputs.shape[1],
                "raw_rul": raw_rul, "business_rul": max(0.0, raw_rul),
                "postprocessing": "max(0, raw_rul)", "rul_unit": "运行周期",
                "input_sha256": input_hash, "model_version": self.manifest["model_version"],
                "best_epoch": self.manifest["best_epoch"], "device": "cpu",
                "duration_ms": (time.perf_counter() - started) * 1000}
