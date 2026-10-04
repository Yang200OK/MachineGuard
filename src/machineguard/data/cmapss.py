"""按旧项目的字段约定读取 FD001，保持全部原始字段。"""

from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd


DatasetSplit = Literal["train", "test"]
RAW_COLUMNS = (
    "engine_id",
    "cycle",
    *(f"setting_{index}" for index in range(1, 4)),
    *(f"sensor_{index}" for index in range(1, 22)),
)


class DatasetError(ValueError):
    """原始文件无法读取或不符合数据完整性要求。"""


@dataclass
class CMAPSSDataset:
    """原始数据及其来源，不生成标签或标准化特征。"""

    dataset: str
    split: DatasetSplit
    records: pd.DataFrame
    source_path: Path
    source_sha256: str
    source_size_bytes: int

    def summarize(self) -> dict:
        """生成供离线数据核对使用的摘要，不提供在线预测答案。"""
        engine_statistics = (
            self.records.groupby("engine_id", sort=True)["cycle"]
            .agg(["min", "max", "count"])
        )
        engines = [
            {
                "engine_id": int(engine_id),
                "first_cycle": int(row["min"]),
                "last_observed_cycle": int(row["max"]),
                "record_count": int(row["count"]),
            }
            for engine_id, row in engine_statistics.iterrows()
        ]
        return {
            "dataset": self.dataset,
            "split": self.split,
            "data_nature": "NASA C-MAPSS 模拟数据",
            "source": {
                "path": str(self.source_path),
                "sha256": self.source_sha256,
                "size_bytes": self.source_size_bytes,
            },
            "row_count": int(len(self.records)),
            "engine_count": int(len(engine_statistics)),
            "column_count": len(RAW_COLUMNS),
            "columns": list(RAW_COLUMNS),
            "cycle_range": {
                "min": int(self.records["cycle"].min()),
                "max": int(self.records["cycle"].max()),
            },
            "validation": {
                "finite_values": True,
                "positive_integer_identifiers": True,
                "unique_engine_cycle_pairs": True,
                "starts_at_cycle_one": True,
                "continuous_cycles": True,
            },
            "engines": engines,
            "scope": "离线原始数据摘要；未生成 RUL 标签、特征选择、标准化或窗口。",
        }


def _check_row_layout(text: str) -> tuple[list[int], list[int]]:
    """先检查每行字段数，防止解析器补 NaN 或误把多余列当索引。"""
    engine_ids: list[int] = []
    cycles: list[int] = []
    max_integer = np.iinfo(np.int64).max
    for line_number, line in enumerate(text.splitlines(), start=1):
        values = line.split()
        # 仅忽略空白行，行为与旧项目的空白分隔读取一致。
        if not values:
            continue
        if len(values) != len(RAW_COLUMNS):
            raise DatasetError(
                f"第 {line_number} 行应有 26 个字段，实际为 {len(values)} 个。"
            )
        identifiers = []
        for field, value in zip(RAW_COLUMNS[:2], values[:2], strict=True):
            # 标识列要求十进制整数，避免浮点读取造成大整数精度丢失。
            if not value.isascii() or not value.isdecimal():
                raise DatasetError(f"第 {line_number} 行的 {field} 必须是正整数。")
            try:
                identifier = int(value)
            except ValueError as error:
                raise DatasetError(f"第 {line_number} 行的 {field} 超出整数范围。") from error
            if not 0 < identifier <= max_integer:
                raise DatasetError(f"第 {line_number} 行的 {field} 超出正整数范围。")
            identifiers.append(identifier)
        engine_ids.append(identifiers[0])
        cycles.append(identifiers[1])
    if not engine_ids:
        raise DatasetError("原始文件没有有效数据行。")
    return engine_ids, cycles


def _validate_and_sort(records: pd.DataFrame) -> pd.DataFrame:
    """检查数值与周期完整性，再按发动机和原始周期排序。"""
    values = records.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        row_index, column_index = np.argwhere(~np.isfinite(values))[0]
        raise DatasetError(
            f"第 {int(row_index) + 1} 条记录的 {RAW_COLUMNS[int(column_index)]} "
            "包含 NaN 或 Inf。"
        )
    duplicate_mask = records.duplicated(subset=["engine_id", "cycle"], keep=False)
    if duplicate_mask.any():
        row = records.loc[duplicate_mask].iloc[0]
        raise DatasetError(
            f"发动机 {int(row['engine_id'])} 的周期 {int(row['cycle'])} 出现重复记录。"
        )

    sorted_records = records.sort_values(
        ["engine_id", "cycle"], ignore_index=True
    )
    for engine_id, engine_records in sorted_records.groupby("engine_id", sort=True):
        engine_cycles = engine_records["cycle"].to_numpy(dtype=np.int64)
        if engine_cycles[0] != 1:
            raise DatasetError(f"发动机 {int(engine_id)} 的首个周期必须为 1。")
        discontinuities = np.flatnonzero(np.diff(engine_cycles) != 1)
        if discontinuities.size:
            index = int(discontinuities[0])
            raise DatasetError(
                f"发动机 {int(engine_id)} 的周期不连续："
                f"{int(engine_cycles[index])} 后为 {int(engine_cycles[index + 1])}。"
            )
    return sorted_records


def load_fd001(source_directory: Path, split: DatasetSplit) -> CMAPSSDataset:
    """读取指定划分；不读取真实测试标签、不补数据、不自动切换文件。"""
    if split not in ("train", "test"):
        raise DatasetError("split 必须为 train 或 test。")
    source_path = (Path(source_directory) / f"{split}_FD001.txt").resolve()
    try:
        raw_bytes = source_path.read_bytes()
    except OSError as error:
        raise DatasetError(f"无法读取原始数据：{source_path}；{error}") from error
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeError as error:
        raise DatasetError(f"原始数据必须是 UTF-8 数值文本：{source_path}") from error

    engine_ids, cycles = _check_row_layout(text)
    try:
        records = pd.read_csv(
            StringIO(text), sep=r"\s+", header=None, dtype=np.float64
        )
    except (ValueError, pd.errors.ParserError) as error:
        raise DatasetError(f"原始数据包含无法解析的数值：{source_path}；{error}") from error
    if records.shape != (len(engine_ids), len(RAW_COLUMNS)):
        raise DatasetError("解析结果与逐行字段检查不一致。")
    records.columns = list(RAW_COLUMNS)
    # 用逐行读取的整数恢复标识列，其它 24 列保留数值。
    records["engine_id"] = np.asarray(engine_ids, dtype=np.int64)
    records["cycle"] = np.asarray(cycles, dtype=np.int64)
    records = _validate_and_sort(records)

    return CMAPSSDataset(
        dataset="FD001",
        split=split,
        records=records,
        source_path=source_path,
        source_sha256=sha256(raw_bytes).hexdigest(),
        source_size_bytes=len(raw_bytes),
    )
