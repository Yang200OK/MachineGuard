"""按发动机划分、训练期标准化并生成可追踪的 FD001 窗口。"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import torch

from ..config import PreparationConfiguration
from .cmapss import CMAPSSDataset, DatasetError, RAW_COLUMNS


# 设备编号仅追踪样本；RUL 和发动机最终周期始终不属于输入特征。
CANDIDATE_FEATURE_COLUMNS = tuple(column for column in RAW_COLUMNS if column != "engine_id")


@dataclass
class WindowCollection:
    """窗口、末端标签及其设备/原始周期身份。"""

    features: torch.Tensor
    targets: torch.Tensor
    engine_ids: torch.Tensor
    start_cycles: torch.Tensor
    end_cycles: torch.Tensor
    skipped_engines: list[dict[str, int]]

    def summarize(self) -> dict:
        """记录维度、标签范围和首末样本身份。"""
        examples = []
        for index in sorted({0, len(self.targets) - 1}):
            examples.append({
                "sample_index": index,
                "engine_id": int(self.engine_ids[index]),
                "start_cycle": int(self.start_cycles[index]),
                "end_cycle": int(self.end_cycles[index]),
                "true_rul": float(self.targets[index]),
            })
        return {
            "window_count": len(self.targets),
            "input_shape": list(self.features.shape),
            "target_shape": list(self.targets.shape),
            "input_dtype": str(self.features.dtype),
            "target_dtype": str(self.targets.dtype),
            "target_range": {
                "min": float(self.targets.min()),
                "max": float(self.targets.max()),
            },
            "skipped_engines": self.skipped_engines,
            "examples": examples,
        }


@dataclass
class PreparedDataset:
    """一次固定数据源、划分与预处理配置的训练准备结果。"""

    source: CMAPSSDataset
    configuration: PreparationConfiguration
    train_engine_ids: tuple[int, ...]
    validation_engine_ids: tuple[int, ...]
    feature_columns: tuple[str, ...]
    constant_columns: tuple[str, ...]
    scaler: StandardScaler
    train_row_count: int
    validation_row_count: int
    train_scaled_mean: np.ndarray
    train_scaled_std: np.ndarray
    train: WindowCollection
    validation: WindowCollection

    def summarize(self) -> dict:
        """保存训练拟合参数及数据身份，供后续训练和推理装配。"""
        return {
            "schema_version": "machineguard.fd001_preparation.v1",
            "created_at": datetime.now(UTC).isoformat(),
            "dataset": self.source.dataset,
            "source_split": self.source.split,
            "source": {
                "path": str(self.source.source_path),
                "sha256": self.source.source_sha256,
                "size_bytes": self.source.source_size_bytes,
            },
            "configuration": asdict(self.configuration),
            "engine_split": {
                "train_engine_ids": list(self.train_engine_ids),
                "validation_engine_ids": list(self.validation_engine_ids),
                "train_engine_count": len(self.train_engine_ids),
                "validation_engine_count": len(self.validation_engine_ids),
                "overlap_count": len(set(self.train_engine_ids) & set(self.validation_engine_ids)),
                "train_row_count": self.train_row_count,
                "validation_row_count": self.validation_row_count,
            },
            "features": {
                "candidate_columns": list(CANDIDATE_FEATURE_COLUMNS),
                "constant_columns": list(self.constant_columns),
                "feature_columns": list(self.feature_columns),
                "feature_count": len(self.feature_columns),
                "selection_scope": "仅训练发动机原始行",
            },
            "scaler": {
                "type": "StandardScaler",
                "fit_scope": "仅训练发动机原始行，窗口展开之前",
                "n_samples_seen": int(self.scaler.n_samples_seen_),
                "mean": self.scaler.mean_.tolist(),
                "variance": self.scaler.var_.tolist(),
                "scale": self.scaler.scale_.tolist(),
                "train_scaled_mean": self.train_scaled_mean.tolist(),
                "train_scaled_std": self.train_scaled_std.tolist(),
            },
            "train": self.train.summarize(),
            "validation": self.validation.summarize(),
            "target_definition": "train_FD001 每台发动机最终周期减窗口末端原始周期；无上限裁剪",
            "scope": "训练文件内按发动机划分的训练/验证准备结果；不是官方测试集评估或在线预测。",
        }


def _make_windows(
    raw_frame: pd.DataFrame,
    feature_columns: tuple[str, ...],
    scaler: StandardScaler,
    configuration: PreparationConfiguration,
) -> WindowCollection:
    """仅复用已拟合 scaler，逐台构造窗口，不跨设备拼接。"""
    scaled = scaler.transform(raw_frame.loc[:, list(feature_columns)])
    # 原始周期负责排序和身份；标准化 cycle 仍作为有序输入特征。
    frame = pd.concat([
        raw_frame[["engine_id", "cycle", "rul"]].rename(columns={"cycle": "raw_cycle"}),
        scaled,
    ], axis=1)
    feature_parts, target_parts = [], []
    engine_parts, start_parts, end_parts = [], [], []
    skipped_engines = []
    window_size = configuration.window_size
    stride = configuration.stride
    for engine_id, engine_frame in frame.groupby("engine_id", sort=True):
        engine_frame = engine_frame.sort_values("raw_cycle")
        cycles = engine_frame["raw_cycle"].to_numpy(dtype=np.int64)
        if len(cycles) < window_size:
            skipped_engines.append({
                "engine_id": int(engine_id), "record_count": len(cycles),
            })
            continue
        if np.any(np.diff(cycles) != 1):
            raise DatasetError(f"发动机 {int(engine_id)} 的原始周期不连续，不能构造窗口。")
        features = torch.from_numpy(
            engine_frame.loc[:, list(feature_columns)].to_numpy(dtype=np.float32)
        )
        targets = torch.from_numpy(engine_frame["rul"].to_numpy(dtype=np.float32))
        if not torch.isfinite(features).all() or not torch.isfinite(targets).all():
            raise DatasetError(f"发动机 {int(engine_id)} 的 FP32 特征或标签存在 NaN/Inf。")
        # unfold 输出为 [窗口数, 特征数, 时间步]，转换成模型所需的维度。
        windows = features.unfold(0, window_size, stride).permute(0, 2, 1)
        labels = targets[window_size - 1::stride]
        starts = np.arange(0, len(cycles) - window_size + 1, stride)
        ends = starts + window_size - 1
        if len(windows) != len(labels) or len(labels) != len(starts):
            raise DatasetError("窗口数量、末端标签与周期身份未对齐。")
        feature_parts.append(windows)
        target_parts.append(labels)
        engine_parts.append(torch.full((len(labels),), int(engine_id), dtype=torch.int64))
        start_parts.append(torch.from_numpy(cycles[starts].copy()))
        end_parts.append(torch.from_numpy(cycles[ends].copy()))
    if not feature_parts:
        raise DatasetError("该划分没有可用窗口；不能用补零或其它设备的数据替代。")
    return WindowCollection(
        features=torch.cat(feature_parts, dim=0).contiguous(),
        targets=torch.cat(target_parts),
        engine_ids=torch.cat(engine_parts),
        start_cycles=torch.cat(start_parts),
        end_cycles=torch.cat(end_parts),
        skipped_engines=skipped_engines,
    )


def prepare_fd001(
    source: CMAPSSDataset, configuration: PreparationConfiguration
) -> PreparedDataset:
    """先划分发动机，再仅用训练原始行拟合特征和标准化。"""
    if source.dataset != "FD001" or source.split != "train":
        raise DatasetError("训练准备只接收 train_FD001，不用测试截断轨迹生成失效标签。")
    if tuple(source.records.columns) != RAW_COLUMNS:
        raise DatasetError("训练准备要求原始 26 列及其顺序。")
    frame = source.records.copy(deep=True)
    frame["rul"] = frame.groupby("engine_id")["cycle"].transform("max") - frame["cycle"]
    engine_ids = sorted(int(value) for value in frame["engine_id"].unique())
    if len(engine_ids) < 2:
        raise DatasetError("至少需要两台发动机，才能进行发动机级训练/验证划分。")
    try:
        train_ids, validation_ids = train_test_split(
            engine_ids, test_size=configuration.validation_size,
            random_state=configuration.random_state, shuffle=configuration.split_shuffle,
        )
    except ValueError as error:
        raise DatasetError(f"发动机划分失败：{error}") from error
    train_ids, validation_ids = tuple(sorted(train_ids)), tuple(sorted(validation_ids))
    if set(train_ids) & set(validation_ids):
        raise DatasetError("训练与验证发动机重叠。")
    train_frame = frame.loc[frame["engine_id"].isin(train_ids)].copy()
    validation_frame = frame.loc[frame["engine_id"].isin(validation_ids)].copy()

    unique_counts = train_frame.loc[:, list(CANDIDATE_FEATURE_COLUMNS)].nunique()
    constant_columns = tuple(unique_counts[unique_counts == 1].index.tolist())
    feature_columns = tuple(
        column for column in CANDIDATE_FEATURE_COLUMNS if column not in constant_columns
    )
    if not feature_columns:
        raise DatasetError("训练集没有可用的非恒定特征。")
    scaler = StandardScaler().set_output(transform="pandas")
    scaler.fit(train_frame.loc[:, list(feature_columns)])
    if (
        not np.isfinite(scaler.mean_).all()
        or not np.isfinite(scaler.scale_).all()
        or np.any(scaler.scale_ <= 0)
    ):
        raise DatasetError("训练拟合的标准化参数非法。")
    train_scaled = scaler.transform(train_frame.loc[:, list(feature_columns)])
    return PreparedDataset(
        source=source, configuration=configuration,
        train_engine_ids=train_ids, validation_engine_ids=validation_ids,
        feature_columns=feature_columns, constant_columns=constant_columns, scaler=scaler,
        train_row_count=len(train_frame), validation_row_count=len(validation_frame),
        train_scaled_mean=train_scaled.mean().to_numpy(),
        train_scaled_std=train_scaled.std(ddof=0).to_numpy(),
        train=_make_windows(train_frame, feature_columns, scaler, configuration),
        validation=_make_windows(validation_frame, feature_columns, scaler, configuration),
    )


def save_prepared_dataset(prepared: PreparedDataset, output_directory: Path) -> tuple[Path, Path]:
    """保存无 pickle 的数组和JSON说明，数组哈希用于后续完整性核对。"""
    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    arrays_path = output_directory / "fd001_windows.npz"
    summary_path = output_directory / "fd001_preparation.json"
    arrays = {}
    for split, collection in (("train", prepared.train), ("validation", prepared.validation)):
        for field in ("features", "targets", "engine_ids", "start_cycles", "end_cycles"):
            arrays[f"{split}_{field}"] = getattr(collection, field).numpy()
    summary = prepared.summarize()
    with TemporaryDirectory(prefix=".preparing-", dir=output_directory) as temporary:
        temporary_directory = Path(temporary)
        temporary_arrays = temporary_directory / arrays_path.name
        np.savez_compressed(temporary_arrays, **arrays)
        summary["artifacts"] = {
            "arrays_file": arrays_path.name,
            "arrays_sha256": sha256(temporary_arrays.read_bytes()).hexdigest(),
            "arrays_size_bytes": temporary_arrays.stat().st_size,
            "array_fields": list(arrays),
        }
        temporary_summary = temporary_directory / summary_path.name
        temporary_summary.write_text(
            json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # 单个文件原子替换；两个文件是否配套必须通过摘要中的哈希核对。
        temporary_arrays.replace(arrays_path)
        temporary_summary.replace(summary_path)
    return arrays_path, summary_path
