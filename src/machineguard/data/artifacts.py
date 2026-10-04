"""从已验收的处理产物加载窗口，不重新拟合预处理参数。"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import TensorDataset

from .cmapss import DatasetError
from .preparation import CANDIDATE_FEATURE_COLUMNS


@dataclass
class TrainingData:
    """同时保留预处理身份与模型使用的数据。"""

    train: TensorDataset
    validation: TensorDataset
    metadata: dict
    fingerprint: dict[str, str]
    identities: dict[str, dict[str, np.ndarray]]


def load_training_data(directory: Path) -> TrainingData:
    """先核对摘要与数组哈希，再检查维度、设备隔离和周期标签。"""
    try:
        manifest_bytes = (directory / "fd001_preparation.json").read_bytes()
        meta = json.loads(manifest_bytes.decode("utf-8"))
        if meta["schema_version"] != "machineguard.fd001_preparation.v1":
            raise DatasetError("不支持的数据处理产物版本。")
        if meta["dataset"] != "FD001" or meta["source_split"] != "train":
            raise DatasetError("训练入口只读取 FD001 训练文件的处理产物。")
        artifact = meta["artifacts"]
        if artifact["arrays_file"] != "fd001_windows.npz":
            raise DatasetError("窗口文件名非法。")
        array_bytes = (directory / artifact["arrays_file"]).read_bytes()
        array_hash = sha256(array_bytes).hexdigest()
        if array_hash != artifact["arrays_sha256"] or len(array_bytes) != artifact["arrays_size_bytes"]:
            raise DatasetError("窗口文件哈希或大小与处理摘要不一致。")
        fields = ("features", "targets", "engine_ids", "start_cycles", "end_cycles")
        expected = {f"{split}_{field}" for split in ("train", "validation") for field in fields}
        columns = meta["features"]["feature_columns"]
        constants = meta["features"]["constant_columns"]
        feature_count = meta["features"]["feature_count"]
        window_size = meta["configuration"]["window_size"]
        stride = meta["configuration"]["stride"]
        if feature_count != len(columns) or len(set(columns)) != len(columns):
            raise DatasetError("特征数量或顺序清单非法。")
        if (meta["features"]["candidate_columns"] != list(CANDIDATE_FEATURE_COLUMNS)
                or not set(constants) <= set(CANDIDATE_FEATURE_COLUMNS)
                or columns != [name for name in CANDIDATE_FEATURE_COLUMNS if name not in constants]):
            raise DatasetError("特征必须保留原始顺序，且只移除训练集识别的常量列。")
        if window_size != 30 or stride != 1 or feature_count != 18:
            raise DatasetError("当前 LSTM 训练要求窗口长度 30、步长 1、有序特征 18 列。")
        split_ids = [meta["engine_split"][f"{split}_engine_ids"] for split in ("train", "validation")]
        if set(split_ids[0]) & set(split_ids[1]):
            raise DatasetError("处理产物的训练与验证发动机重叠。")
        for ids in split_ids:
            if not ids or ids != sorted(set(ids)) or any(type(i) is not int or i <= 0 for i in ids):
                raise DatasetError("发动机清单非法。")
        scaler = meta["scaler"]
        if scaler["type"] != "StandardScaler" or scaler["n_samples_seen"] != meta["engine_split"]["train_row_count"]:
            raise DatasetError("标准化拟合范围不符合训练行契约。")
        for name in ("mean", "variance", "scale"):
            values = np.asarray(scaler[name], dtype=np.float64)
            if values.shape != (feature_count,) or not np.isfinite(values).all():
                raise DatasetError("标准化参数形状或有限性非法。")
            if name != "mean" and np.any(values <= 0):
                raise DatasetError("保留特征的方差和标准差必须为正。")
        datasets, identities = [], {}
        with np.load(BytesIO(array_bytes), allow_pickle=False) as arrays:
            if set(arrays.files) != expected or set(artifact["array_fields"]) != expected:
                raise DatasetError("窗口数组字段不完整。")
            for split, ids in zip(("train", "validation"), split_ids, strict=True):
                x, y, engine, start, end = [arrays[f"{split}_{field}"] for field in fields]
                count = meta[split]["window_count"]
                if count <= 0 or x.shape != (count, window_size, feature_count) or y.shape != (count,):
                    raise DatasetError(f"{split} 窗口形状不匹配。")
                if x.dtype != np.float32 or y.dtype != np.float32 or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(y < 0):
                    raise DatasetError(f"{split} 输入/标签必须为有限 FP32，原始 RUL 不能为负。")
                for identity in (engine, start, end):
                    if identity.shape != (count,) or identity.dtype != np.int64:
                        raise DatasetError(f"{split} 窗口身份非法。")
                if not set(engine.tolist()) <= set(ids) or np.any(np.diff(engine) < 0) or np.any(start <= 0) or np.any(end - start != window_size - 1):
                    raise DatasetError(f"{split} 设备或窗口跨度非法。")
                for unit in np.unique(engine):
                    mask = engine == unit
                    if np.any(np.diff(start[mask]) != stride) or np.any(y[mask] + end[mask] != (y[mask] + end[mask])[0]):
                        raise DatasetError(f"{split} 周期顺序或末端标签不一致。")
                datasets.append(TensorDataset(torch.from_numpy(x.copy()), torch.from_numpy(y.copy())))
                # 同步保留已经核对的身份，评估不能再次读取未核对或其它目录的数组。
                identities[split] = {"engine_id": engine.copy(), "start_cycle": start.copy(), "end_cycle": end.copy()}
        return TrainingData(*datasets, metadata=meta, fingerprint={
            "preparation_sha256": sha256(manifest_bytes).hexdigest(),
            "arrays_sha256": array_hash, "source_sha256": meta["source"]["sha256"],
        }, identities=identities)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, DatasetError):
            raise
        raise DatasetError(f"无法加载训练处理产物：{error}") from error
