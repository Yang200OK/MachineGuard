"""固定训练目标与可序列化的回归参数。"""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from .config import ConfigurationError


@dataclass(frozen=True)
class TrainingConfiguration:
    """正式训练始终累计完成 200 轮，没有 early stopping。"""

    epochs: int = 200
    seed: int = 42
    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.2
    batch_size: int = 64
    learning_rate: float = 0.001
    weight_decay: float = 1e-5
    gradient_clip: float = 5.0
    scheduler_patience: int = 5
    scheduler_factor: float = 0.5
    min_learning_rate: float = 1e-5
    cpu_threads: int = 4

    def __post_init__(self) -> None:
        for name in ("epochs", "hidden_size", "num_layers", "batch_size", "cpu_threads"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ConfigurationError(f"{name} 必须是正整数。")
        if self.epochs != 200:
            raise ConfigurationError("正式训练目标必须是 200 轮。快速核对用 --smoke。")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ConfigurationError("seed 必须是有效的非负随机种子。")
        if type(self.scheduler_patience) is not int or self.scheduler_patience < 0:
            raise ConfigurationError("scheduler_patience 必须是非负整数。")
        for name in ("dropout", "learning_rate", "weight_decay", "gradient_clip",
                     "scheduler_factor", "min_learning_rate"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigurationError(f"{name} 必须是有限数值。")
        if not 0 <= self.dropout < 1 or (self.num_layers == 1 and self.dropout != 0):
            raise ConfigurationError("dropout 必须在 [0,1)；单层 LSTM 应设为 0。")
        if self.learning_rate <= 0 or self.weight_decay < 0 or self.gradient_clip <= 0:
            raise ConfigurationError("学习率和梯度上限必须为正，weight_decay 不能为负。")
        if not 0 < self.scheduler_factor < 1:
            raise ConfigurationError("scheduler_factor 必须在 (0,1)。")
        if not 0 < self.min_learning_rate <= self.learning_rate:
            raise ConfigurationError("最小学习率必须为正且不超过初始学习率。")


def load_training_configuration(path: Path) -> TrainingConfiguration:
    """拒绝缺失、未知参数和隐式默认替换。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"无法读取训练配置：{path}；{error}") from error
    if not isinstance(payload, dict) or set(payload) != set(asdict(TrainingConfiguration())):
        raise ConfigurationError("训练配置字段必须与 configs/training.json 完整一致。")
    return TrainingConfiguration(**payload)
