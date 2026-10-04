"""组织固定训练、轮末保存与同一次运行的恢复。"""

from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import random
import time
from uuid import uuid4

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

from .checkpoints import (atomic_torch_save, capture_random_state, cpu_snapshot,
                          load_checkpoint, load_model_checkpoint, restore_random_state)
from .data.artifacts import TrainingData
from .models import LSTMRULRegressor
from .training import TrainingError, run_epoch
from .training_config import TrainingConfiguration


def _write_json(payload: dict, path: Path) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_training(data: TrainingData, config: TrainingConfiguration, artifact_root: Path,
                 device_name: str, smoke: bool = False, resume: Path | None = None,
                 stop_after_epoch: int | None = None) -> Path:
    """smoke 为 65/65 个真实窗口、2 轮；正式任务目标仍为 200 轮。"""
    if device_name not in ("cpu", "cuda"):
        raise TrainingError("device 必须显式选择 cpu 或 cuda。")
    if device_name == "cuda" and not torch.cuda.is_available():
        raise TrainingError("要求 CUDA，但 CUDA 不可用。")
    device = torch.device(device_name)
    mode = "smoke" if smoke else "training"
    target_epoch = 2 if smoke else config.epochs
    if stop_after_epoch is not None and (type(stop_after_epoch) is not int or not 1 <= stop_after_epoch <= target_epoch):
        raise TrainingError("暂停轮数必须在本次运行目标范围内。")
    final_epoch = target_epoch if stop_after_epoch is None else stop_after_epoch

    # 固定进程内计算配置；硬件/版本不同不承诺逐位相同。
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    torch.set_num_threads(config.cpu_threads)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.use_deterministic_algorithms(True)
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    generator = torch.Generator().manual_seed(config.seed)
    model_configuration = {"input_size": data.metadata["features"]["feature_count"],
                           "hidden_size": config.hidden_size, "num_layers": config.num_layers,
                           "dropout": config.dropout}
    runtime = {"torch": str(torch.__version__), "numpy": str(np.__version__),
               "device": device_name, "cpu_threads": config.cpu_threads,
               "cuda_version": torch.version.cuda,
               "device_name": torch.cuda.get_device_name(0) if device_name == "cuda" else "CPU"}
    model = LSTMRULRegressor(**model_configuration).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", patience=config.scheduler_patience,
        factor=config.scheduler_factor, min_lr=config.min_learning_rate)

    train_dataset, validation_dataset = data.train, data.validation
    if smoke:
        # 包含批量 64 和末批 1，核对 squeeze 与按样本累计指标。
        if min(len(train_dataset), len(validation_dataset)) < 65:
            raise TrainingError("快速核对需要训练与验证各至少 65 个真实窗口。")
        train_dataset = Subset(train_dataset, range(65))
        validation_dataset = Subset(validation_dataset, range(65))
    train_loader = DataLoader(train_dataset, batch_size=config.batch_size, shuffle=True,
                              num_workers=0, generator=generator, drop_last=False)
    validation_loader = DataLoader(validation_dataset, batch_size=config.batch_size, shuffle=False,
                                   num_workers=0, generator=torch.Generator().manual_seed(config.seed), drop_last=False)
    history, best_state, best_metrics, best_epoch, start_epoch = [], None, None, 0, 1
    if resume is None:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        run_directory = artifact_root.resolve() / mode / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
    else:
        checkpoint = load_checkpoint(resume)
        if checkpoint["kind"] != "last":
            raise TrainingError("恢复训练只接受 last，best 用于推理。")
        comparisons = {"mode": mode, "configuration": asdict(config), "fingerprint": data.fingerprint,
                       "model_configuration": model_configuration, "runtime": runtime}
        for field, value in comparisons.items():
            if checkpoint[field] != value:
                raise TrainingError(f"恢复检查失败：{field} 与原运行不同。")
        if resume.name != "last.pt":
            raise TrainingError("必须使用原运行目录中的 last.pt 恢复。")
        run_directory, run_id = resume.resolve().parent, checkpoint["run_id"]
        model.load_state_dict(checkpoint["model_state"], strict=True)
        optimizer.load_state_dict(checkpoint["optimizer_state"])
        scheduler.load_state_dict(checkpoint["scheduler_state"])
        history = checkpoint["history"]
        start_epoch = checkpoint["epoch"] + 1
        best_state, best_metrics, best_epoch = checkpoint["best_state"], checkpoint["best_metrics"], checkpoint["best_epoch"]
        if len(history) != start_epoch - 1 or [row["epoch"] for row in history] != list(range(1, start_epoch)):
            raise TrainingError("检查点轮数与训练历史不一致。")
        restore_random_state(checkpoint["random_state"], generator, device)
    if start_epoch > final_epoch:
        raise TrainingError("last 已达到指定轮数；没有新的训练轮可执行。")
    base = {"schema_version": "machineguard.lstm_checkpoint.v1", "run_id": run_id,
            "mode": mode, "configuration": asdict(config), "model_configuration": model_configuration,
            "fingerprint": data.fingerprint, "preprocessing": data.metadata, "runtime": runtime,
            "target_epochs": target_epoch, "early_stopping": False}
    print(f"运行模式：{mode}；目标：{target_epoch} 轮；本次执行至：{final_epoch} 轮；起始：{start_epoch}")
    print(f"设备：{runtime['device_name']}；参数量：{sum(p.numel() for p in model.parameters())}；FP32")
    print(f"使用窗口：训练 {len(train_dataset)}，验证 {len(validation_dataset)}；输出：{run_directory}")
    for epoch in range(start_epoch, final_epoch + 1):
        started = time.perf_counter()
        lr_used = optimizer.param_groups[0]["lr"]
        train_metrics = run_epoch(model, train_loader, device, optimizer, config.gradient_clip)
        validation_metrics = run_epoch(model, validation_loader, device)
        scheduler.step(validation_metrics["rmse"])
        if best_metrics is None or validation_metrics["rmse"] < best_metrics["rmse"]:
            best_state, best_metrics, best_epoch = cpu_snapshot(model.state_dict()), validation_metrics.copy(), epoch
        history.append({"epoch": epoch, "train_online": train_metrics, "validation": validation_metrics,
                        "learning_rate_used": lr_used, "learning_rate_next": optimizer.param_groups[0]["lr"],
                        "duration_seconds": time.perf_counter() - started})
        last = {**base, "kind": "last", "epoch": epoch, "model_state": cpu_snapshot(model.state_dict()),
                "optimizer_state": cpu_snapshot(optimizer.state_dict()), "scheduler_state": scheduler.state_dict(),
                "random_state": capture_random_state(generator, device), "history": history,
                "best_state": best_state, "best_metrics": best_metrics, "best_epoch": best_epoch}
        # last 是恢复依据，也保留 best 快照；其它文件落后时可从 last 重建。
        atomic_torch_save(last, run_directory / "last.pt")
        atomic_torch_save({**base, "kind": "best", "epoch": best_epoch, "model_state": best_state,
                           "validation": best_metrics}, run_directory / "best.pt")
        _write_json({**{key: base[key] for key in ("run_id", "mode", "target_epochs", "early_stopping", "configuration", "fingerprint", "runtime")},
                     "completed_epochs": epoch, "status": ("complete" if epoch == target_epoch else
                                                            "paused" if epoch == final_epoch else "running"),
                     "history": history, "best_epoch": best_epoch, "best_validation": best_metrics}, run_directory / "history.json")
        print(f"轮 {epoch}/{target_epoch}：训练 RMSE {train_metrics['rmse']:.6f}；验证 RMSE {validation_metrics['rmse']:.6f}；"
              f"验证 MAE {validation_metrics['mae']:.6f}；lr {lr_used:.6g}；best {best_epoch}", flush=True)

    # 核对同一 last 在 CPU 的保存/加载一致性；不会用快速指标宣称模型质量。
    cpu_model, _ = load_model_checkpoint(run_directory / "last.pt")
    reference = LSTMRULRegressor(**model_configuration)
    reference.load_state_dict(cpu_snapshot(model.state_dict()), strict=True)
    reference.eval()
    example = data.validation.tensors[0][:1]
    with torch.inference_mode():
        expected, actual = reference(example), cpu_model(example)
    if actual.shape != (1,) or not torch.equal(expected, actual):
        raise TrainingError("CPU 检查点重载结果与内存权重不一致。")
    _write_json({"mode": mode, "checkpoint": "last.pt", "batch_one_shape": list(actual.shape),
                 "cpu_reload_max_absolute_error": float((expected - actual).abs().max()),
                 "raw_rul": float(actual[0]), "scope": "标准化窗口上的 CPU 重载核对，非模型质量评估。"},
                run_directory / "reload_check.json")
    print("CPU 保存/加载核对：通过；批量 1 输出 [1]；重载误差 0。")
    print("快速核对结束；不是正式 200 轮训练或官方测试评估。" if smoke else
          ("已累计完成 200 轮。" if final_epoch == target_epoch else "本次已暂停；继续时使用同目录 last.pt。"))
    return run_directory
