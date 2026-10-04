"""读取数据配置，缺失或非法配置直接报错。"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .paths import get_project_root


class ConfigurationError(ValueError):
    """配置内容不符合当前数据读取契约。"""


@dataclass(frozen=True)
class DataConfiguration:
    """保存已解析为绝对路径的数据配置。"""

    dataset: str
    source_directory: Path
    summary_directory: Path


def _resolve_project_path(value: str, project_root: Path) -> Path:
    """相对路径统一以当前项目根目录为起点。"""
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def load_data_configuration(config_path: Path) -> DataConfiguration:
    """读取 UTF-8 JSON 配置；不自动加载环境变量或替代配置。"""
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            payload = json.load(config_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"无法读取数据配置：{config_path}；{error}") from error

    expected_fields = {"dataset", "source_directory", "summary_directory"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ConfigurationError(
            "数据配置必须且只能包含 dataset、source_directory、summary_directory。"
        )
    for field in expected_fields:
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ConfigurationError(f"配置字段 {field} 必须是非空字符串。")
    if payload["dataset"] != "FD001":
        raise ConfigurationError("当前阶段仅支持 FD001，不自动改用其它数据集。")

    project_root = get_project_root()
    return DataConfiguration(
        dataset=payload["dataset"],
        source_directory=_resolve_project_path(payload["source_directory"], project_root),
        summary_directory=_resolve_project_path(payload["summary_directory"], project_root),
    )


@dataclass(frozen=True)
class PreparationConfiguration:
    """记录发动机划分和窗口参数，拒绝布尔值冒充整数。"""

    validation_size: float
    random_state: int
    split_shuffle: bool
    window_size: int
    stride: int

    def __post_init__(self) -> None:
        if (
            type(self.validation_size) not in (int, float)
            or not 0 < self.validation_size < 1
            or not math.isfinite(self.validation_size)
        ):
            raise ConfigurationError("validation_size 必须是 0 与 1 之间的有限数值。")
        if type(self.random_state) is not int or not 0 <= self.random_state < 2**32:
            raise ConfigurationError("random_state 必须是 0 至 2**32-1 之间的整数。")
        if type(self.split_shuffle) is not bool:
            raise ConfigurationError("split_shuffle 必须是布尔值。")
        for field in ("window_size", "stride"):
            value = getattr(self, field)
            if type(value) is not int or value <= 0:
                raise ConfigurationError(f"{field} 必须是正整数。")


def load_preparation_configuration(config_path: Path) -> PreparationConfiguration:
    """读取独立的处理配置，保持原始读取配置与入口不变。"""
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            payload = json.load(config_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"无法读取数据处理配置：{config_path}；{error}") from error
    expected_fields = {
        "validation_size", "random_state", "split_shuffle", "window_size", "stride"
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ConfigurationError(
            "数据处理配置必须且只能包含 validation_size、random_state、"
            "split_shuffle、window_size、stride。"
        )
    return PreparationConfiguration(**payload)
