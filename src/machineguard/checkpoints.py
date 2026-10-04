"""保存独立 CPU 快照与完整轮边界的随机状态。"""

from pathlib import Path
import pickle
import random
from tempfile import NamedTemporaryFile

import numpy as np
import torch

from .models import LSTMRULRegressor
from .training import TrainingError


def cpu_snapshot(value):
    """递归复制状态，防止 best 权重随后续训练改变。"""
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: cpu_snapshot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cpu_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return tuple(cpu_snapshot(item) for item in value)
    return value


def atomic_torch_save(payload: dict, path: Path) -> None:
    """Windows 下先关闭临时文件句柄，再原子替换目标。"""
    with NamedTemporaryFile(dir=path.parent, prefix="checkpoint-", suffix=".pt", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        torch.save(payload, temporary_path)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def capture_random_state(generator: torch.Generator, device: torch.device) -> dict:
    """NumPy 状态转成基础类型，支持 weights_only 加载。"""
    state = np.random.get_state()
    return {"python": random.getstate(), "numpy": (state[0], state[1].tolist(), *state[2:]),
            "torch": torch.get_rng_state(), "loader": generator.get_state(),
            "cuda": torch.cuda.get_rng_state_all() if device.type == "cuda" else []}


def restore_random_state(state: dict, generator: torch.Generator, device: torch.device) -> None:
    """模型、优化器和加载器重建完成后，再恢复随机状态。"""
    random.setstate(state["python"])
    array_state = state["numpy"]
    np.random.set_state((array_state[0], np.asarray(array_state[1], dtype=np.uint32), *array_state[2:]))
    torch.set_rng_state(state["torch"])
    generator.set_state(state["loader"])
    if device.type == "cuda":
        torch.cuda.set_rng_state_all(state["cuda"])


def load_checkpoint(path: Path) -> dict:
    """仅加载本项目张量与基础类型，不允许通用 pickle 对象。"""
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError, EOFError, pickle.UnpicklingError) as error:
        raise TrainingError(f"无法加载检查点：{path}；{error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != "machineguard.lstm_checkpoint.v1":
        raise TrainingError("不支持的模型检查点版本。")
    if payload.get("kind") not in ("best", "last"):
        raise TrainingError("检查点类型非法。")
    required = {"run_id", "mode", "configuration", "model_configuration", "fingerprint",
                "preprocessing", "runtime", "target_epochs", "epoch", "model_state"}
    required |= ({"optimizer_state", "scheduler_state", "random_state", "history",
                  "best_state", "best_metrics", "best_epoch"} if payload["kind"] == "last" else {"validation"})
    if not required <= payload.keys() or payload["mode"] not in ("smoke", "training"):
        raise TrainingError("检查点字段缺失或模式非法。")
    if type(payload["epoch"]) is not int or not 1 <= payload["epoch"] <= payload["target_epochs"]:
        raise TrainingError("检查点累计轮数非法。")
    return payload


def load_model_checkpoint(path: Path) -> tuple[LSTMRULRegressor, dict]:
    """部署方向使用 CPU，特征与 scaler 随权重保存在检查点中。"""
    payload = load_checkpoint(path)
    model = LSTMRULRegressor(**payload["model_configuration"])
    model.load_state_dict(payload["model_state"], strict=True)
    model.eval()
    return model, payload
