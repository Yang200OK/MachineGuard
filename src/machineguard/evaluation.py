"""正式训练结束后的固定评估、离线 Ridge 对照和产物交付。"""

from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import sys
from zipfile import ZIP_DEFLATED, ZipFile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge
import torch
from torch.utils.data import DataLoader, TensorDataset

from .checkpoints import atomic_torch_save, load_checkpoint, load_model_checkpoint
from .data.artifacts import TrainingData
from .data.cmapss import DatasetError, load_fd001
from .inference import LSTMPredictor, standardize_window
from .model_artifacts import file_record, publish_manifest, verify_manifest, write_json
from .training import TrainingError
from .training_config import TrainingConfiguration


def regression_metrics(targets: np.ndarray, predictions: np.ndarray) -> dict:
    """在相同样本上计算原始标签误差，不取批量均值的平均。"""
    targets, predictions = np.asarray(targets, dtype=np.float64), np.asarray(predictions, dtype=np.float64)
    if targets.ndim != 1 or targets.shape != predictions.shape or not len(targets):
        raise TrainingError("评估标签与预测必须为同长非空一维数组。")
    if not np.isfinite(targets).all() or not np.isfinite(predictions).all():
        raise TrainingError("评估出现非有限标签或预测。")
    errors = predictions - targets
    mse, mae = float(np.square(errors).mean()), float(np.abs(errors).mean())
    if not math.isfinite(mse) or not math.isfinite(mae):
        raise TrainingError("回归误差溢出，不能导出非有限指标。")
    return {"samples": len(targets), "mae": mae, "mse": mse, "rmse": math.sqrt(mse)}


def _describe(targets: np.ndarray, predictions: np.ndarray) -> dict:
    """分别记录原始和非负结果，并按真实 RUL 范围统计误差。"""
    bins = []
    for lower, upper in ((0, 30), (30, 60), (60, 120), (120, None)):
        mask = (targets >= lower) & (targets < upper if upper is not None else True)
        bins.append({"lower_inclusive": lower, "upper_exclusive": upper,
                     "raw": regression_metrics(targets[mask], predictions[mask]) if mask.any() else {"samples": 0}})
    return {"raw": regression_metrics(targets, predictions),
            "nonnegative": regression_metrics(targets, np.maximum(0, predictions)),
            "raw_prediction_range": {"min": float(predictions.min()), "max": float(predictions.max())},
            "target_range": {"min": float(targets.min()), "max": float(targets.max())},
            "negative_prediction_count": int((predictions < 0).sum()), "intervals": bins}


def _predict(model, inputs: torch.Tensor, batch_size: int) -> np.ndarray:
    model.eval()
    parts = []
    with torch.inference_mode():
        for (batch,) in DataLoader(TensorDataset(inputs), batch_size=batch_size, shuffle=False, num_workers=0):
            parts.append(model(batch).cpu().numpy())
    result = np.concatenate(parts)
    if not np.isfinite(result).all():
        raise TrainingError("CPU 批量评估产生 NaN/Inf。")
    return result


def _terminal_test(source_directory: Path, preprocessing: dict) -> tuple[torch.Tensor, np.ndarray, pd.DataFrame, dict]:
    """只在离线模块读取官方末端标签，每台发动机取末端一个窗口。"""
    source = load_fd001(source_directory, "test")
    label_path = source_directory / "RUL_FD001.txt"
    content = label_path.read_bytes()
    values = []
    for line_number, line in enumerate(content.decode("utf-8-sig").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 1:
            raise DatasetError(f"测试标签第 {line_number} 行必须只有一个 RUL。")
        try:
            value = float(fields[0])
        except ValueError as error:
            raise DatasetError(f"测试标签第 {line_number} 行不是数值。") from error
        if not math.isfinite(value) or value < 0:
            raise DatasetError(f"测试标签第 {line_number} 行必须是有限非负 RUL。")
        values.append(value)
    ids = sorted(int(value) for value in source.records["engine_id"].unique())
    # 官方行号对应发动机编号，拒绝靠压缩后的清单推测缺失编号。
    if ids != list(range(1, len(ids) + 1)) or len(values) != len(ids):
        raise DatasetError("官方测试发动机编号与 RUL 标签行数不匹配。")
    inputs, targets, identities, skipped = [], [], [], []
    size = preprocessing["configuration"]["window_size"]
    for engine_id, frame in source.records.groupby("engine_id", sort=True):
        engine_id = int(engine_id)
        end_cycle = int(frame["cycle"].max())
        if len(frame) < size:
            skipped.append({"engine_id": engine_id, "record_count": len(frame)})
            continue
        window = frame.tail(size)
        tensor, input_hash = standardize_window(window, preprocessing, engine_id, end_cycle)
        inputs.append(tensor)
        targets.append(values[engine_id - 1])
        identities.append({"engine_id": engine_id, "start_cycle": end_cycle - size + 1,
                           "end_cycle": end_cycle, "input_sha256": input_hash})
    if not inputs:
        raise DatasetError("官方测试集没有可形成窗口的设备。")
    return torch.cat(inputs), np.asarray(targets, dtype=np.float64), pd.DataFrame(identities), {
        "test_source": {"path": str(source.source_path), "sha256": source.source_sha256},
        "labels_source": {"path": str(label_path.resolve()), "sha256": sha256(content).hexdigest()},
        "evaluation_scope": "每台测试发动机最后观测周期的单个窗口；标签直接取官方 RUL 向量。",
        "engine_count": len(ids), "evaluated_engine_count": len(targets), "skipped_engines": skipped}


def _prediction_table(identities: pd.DataFrame, targets: np.ndarray, predictions: np.ndarray) -> pd.DataFrame:
    table = identities.copy(deep=True)
    table["true_rul"] = targets
    table["raw_rul"] = predictions
    table["business_rul"] = np.maximum(0, predictions)
    table["error_raw"] = predictions - targets
    table["absolute_error_raw"] = np.abs(predictions - targets)
    return table


def _write_csv(table: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(".csv.tmp")
    table.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)


def _curves(history: list, directory: Path) -> None:
    table = pd.DataFrame([{"epoch": row["epoch"], "train_online_mse": row["train_online"]["mse"],
                           "train_online_rmse": row["train_online"]["rmse"], "train_online_mae": row["train_online"]["mae"],
                           "validation_mse": row["validation"]["mse"], "validation_rmse": row["validation"]["rmse"],
                           "validation_mae": row["validation"]["mae"], "learning_rate_used": row["learning_rate_used"],
                           "duration_seconds": row["duration_seconds"]} for row in history])
    _write_csv(table, directory / "training_history.csv")
    figure, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    for ax, metric in zip(axes.flat[:3], ("rmse", "mae", "mse"), strict=True):
        ax.plot(table.epoch, table[f"train_online_{metric}"], label="Train (online)")
        ax.plot(table.epoch, table[f"validation_{metric}"], label="Validation")
        ax.set(xlabel="Epoch", ylabel=metric.upper())
        ax.grid(alpha=0.2)
        ax.legend()
    axes[1, 1].plot(table.epoch, table.learning_rate_used)
    axes[1, 1].set(xlabel="Epoch", ylabel="Learning rate", yscale="log")
    axes[1, 1].grid(alpha=0.2)
    figure.suptitle("MachineGuard FD001 - 200 epochs, no early stopping")
    temporary = directory / "training_curve.tmp.png"
    figure.savefig(temporary, dpi=160)
    plt.close(figure)
    temporary.replace(directory / "training_curve.png")


def _source_snapshot(project_root: Path, directory: Path) -> list[dict]:
    """归档实际实现与配置，不依赖尚未创建的 Git 提交或参考目录。"""
    paths = [project_root / "pyproject.toml"]
    for folder, suffix in (("src", ".py"), ("scripts", ".py"), ("configs", ".json")):
        paths.extend(sorted((project_root / folder).rglob(f"*{suffix}")))
    records = []
    temporary = directory / "source_snapshot.tmp.zip"
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            relative = path.relative_to(project_root).as_posix()
            content = path.read_bytes()
            archive.writestr(relative, content)
            records.append({"file": relative, "sha256": sha256(content).hexdigest(), "size_bytes": len(content)})
    temporary.replace(directory / "source_snapshot.zip")
    return records


def evaluate_and_export(run_directory: Path, data: TrainingData, source_directory: Path, project_root: Path) -> dict:
    """只接受完整正式运行，固定 best 后评估测试集，发布离线与部署清单。"""
    last = load_checkpoint(run_directory / "last.pt")
    model, best = load_model_checkpoint(run_directory / "best.pt")
    if last["kind"] != "last" or best["kind"] != "best" or last["mode"] != "training" or best["mode"] != "training":
        raise TrainingError("正式评估只接受 training 模式的 best/last。")
    if last["epoch"] != 200 or last["target_epochs"] != 200 or last["early_stopping"] is not False:
        raise TrainingError("必须完成 200 轮且关闭 early stopping 才能交付。")
    if (len(last["history"]) != 200 or [row["epoch"] for row in last["history"]] != list(range(1, 201))
            or best["run_id"] != last["run_id"] or best["epoch"] != last["best_epoch"]
            or best["configuration"] != last["configuration"] or best["preprocessing"] != data.metadata
            or best["fingerprint"] != data.fingerprint or last["fingerprint"] != data.fingerprint):
        raise TrainingError("训练历史、best、配置或当前处理数据不一致。")
    selected = min(last["history"], key=lambda row: row["validation"]["rmse"])
    if selected["epoch"] != best["epoch"] or selected["validation"] != best["validation"]:
        raise TrainingError("best 不是原始验证 RMSE 最低的轮。")
    if set(best["model_state"]) != set(last["best_state"]) or any(
            not torch.equal(value, last["best_state"][name]) for name, value in best["model_state"].items()):
        raise TrainingError("best 权重与 last 中冻结的最佳快照不一致。")
    config = TrainingConfiguration(**best["configuration"])
    torch.set_num_threads(config.cpu_threads)
    train_x, train_y = data.train.tensors
    validation_x, validation_y = data.validation.tensors
    train_predictions = _predict(model, train_x, config.batch_size)
    validation_predictions = _predict(model, validation_x, config.batch_size)
    validation_result = _describe(validation_y.numpy(), validation_predictions)
    if not math.isclose(validation_result["raw"]["rmse"], best["validation"]["rmse"], rel_tol=1e-5, abs_tol=1e-4):
        raise TrainingError("CPU 完整验证集 RMSE 与最佳轮记录不一致。")
    test_x, test_y, test_ids, test_meta = _terminal_test(source_directory, best["preprocessing"])
    test_predictions = _predict(model, test_x, config.batch_size)
    # 验证身份从已经通过哈希核对的数组读取，保持与标签相同顺序。
    validation_ids = pd.DataFrame(data.identities["validation"])
    validation_table = _prediction_table(validation_ids, validation_y.numpy(), validation_predictions)
    test_table = _prediction_table(test_ids, test_y, test_predictions)
    # Ridge 只做离线比较：同一标签、划分、窗口、scaler，展平 30x18 输入；alpha 固定为 1。
    ridge = Ridge(alpha=1.0, solver="cholesky")
    ridge.fit(train_x.numpy().reshape(len(train_x), -1).astype(np.float64), train_y.numpy().astype(np.float64))
    ridge_validation = ridge.predict(validation_x.numpy().reshape(len(validation_x), -1).astype(np.float64))
    ridge_test = ridge.predict(test_x.numpy().reshape(len(test_x), -1).astype(np.float64))
    metrics = {"schema_version": "machineguard.fd001_evaluation.v1", "model_version": best["run_id"],
               "created_at": datetime.now(UTC).isoformat(), "completed_epochs": 200, "best_epoch": best["epoch"],
               "selection": "只按训练文件内发动机隔离的验证集原始 RMSE 选 best；官方测试标签不参与训练或选模。",
               "training_best_frozen": _describe(train_y.numpy(), train_predictions),
               "validation": validation_result, "official_test_terminal": {**_describe(test_y, test_predictions), **test_meta},
               "ridge_baseline": {"scope": "仅离线，不是部署模型；不是旧项目历史指标。", "alpha": 1.0,
                                  "solver": "cholesky", "input": "同一标准化 30x18 窗口展平为 540 维", "sklearn": str(sklearn.__version__),
                                  "validation": _describe(validation_y.numpy(), ridge_validation),
                                  "official_test_terminal": _describe(test_y, ridge_test)},
               "worst_validation_cases": validation_table.nlargest(10, "absolute_error_raw").to_dict(orient="records"),
               "worst_test_cases": test_table.nlargest(10, "absolute_error_raw").to_dict(orient="records"),
               "limitations": ["数据为 FD001 模拟设备，RUL 单位为运行周期。", "固定单次 seed=42，未测多种子稳定性。",
                               "训练指标来自更新过程；training_best_frozen 是 best 权重的固定评估。",
                               "预测与传感器变化不能证明具体部件故障。", "官方测试仅用于固定方案评估，不据此反复调参。"]}
    write_json(metrics, run_directory / "evaluation.json")
    _write_csv(validation_table, run_directory / "validation_predictions.csv")
    _write_csv(test_table, run_directory / "test_predictions.csv")
    _write_csv(_prediction_table(validation_ids, validation_y.numpy(), ridge_validation), run_directory / "ridge_validation_predictions.csv")
    _write_csv(_prediction_table(test_ids, test_y, ridge_test), run_directory / "ridge_test_predictions.csv")
    _curves(last["history"], run_directory)
    preprocessing = {key: best["preprocessing"][key] for key in ("schema_version", "dataset", "source_split", "source", "configuration", "engine_split", "features", "scaler")}
    for filename, value in (("training_config.json", asdict(config)), ("preprocessing.json", preprocessing),
                            ("engine_split.json", preprocessing["engine_split"]), ("feature_columns.json", preprocessing["features"]["feature_columns"]),
                            ("scaler.json", preprocessing["scaler"])):
        write_json(value, run_directory / filename)
    bundle = run_directory / "deployment"
    bundle.mkdir(exist_ok=True)
    # 部署包不复制训练样本标签、测试标签、评估 CSV 或 last 的优化器状态。
    lean = {key: value for key, value in best.items() if key != "preprocessing"}
    lean["preprocessing"] = preprocessing
    atomic_torch_save(lean, bundle / "model.pt")
    write_json(preprocessing, bundle / "preprocessing.json")
    publish_manifest({"schema_version": "machineguard.lstm_deployment.v1", "model_version": best["run_id"],
                      "training_mode": "training", "completed_epochs": 200, "best_epoch": best["epoch"],
                      "device": "cpu", "postprocessing": "max(0, raw_rul)", "fingerprint": best["fingerprint"],
                      "files": [file_record(bundle / name) for name in ("model.pt", "preprocessing.json")]}, bundle)
    source_files = _source_snapshot(project_root, run_directory)
    write_json({"training_runtime": last["runtime"], "evaluation_device": "cpu",
                "python": sys.version, "python_executable": sys.executable, "platform": platform.platform(),
                "numpy": str(np.__version__), "sklearn": str(sklearn.__version__), "matplotlib": str(matplotlib.__version__),
                "training_computation": {"precision": "FP32", "tf32": False, "deterministic_algorithms": True,
                                         "num_workers": 0, "seed": config.seed}}, run_directory / "environment.json")
    filenames = ["best.pt", "last.pt", "history.json", "reload_check.json", "evaluation.json", "validation_predictions.csv",
                 "test_predictions.csv", "ridge_validation_predictions.csv", "ridge_test_predictions.csv", "training_curve.png",
                 "training_history.csv", "training_config.json", "preprocessing.json", "engine_split.json", "feature_columns.json", "scaler.json", "source_snapshot.zip", "environment.json"]
    publish_manifest({"schema_version": "machineguard.lstm_delivery.v1", "model_version": best["run_id"],
                      "completed_epochs": 200, "best_epoch": best["epoch"], "fingerprint": best["fingerprint"],
                      "runtime": last["runtime"], "source_files": source_files,
                      "deployment_manifest_sha256": file_record(bundle / "model_manifest.json")["sha256"],
                      "scope": "包含真实标签的离线评估交付；部署只使用 deployment 子目录。",
                      "files": [file_record(run_directory / name) for name in filenames]}, run_directory)
    return metrics


def verify_delivery(run_directory: Path) -> dict:
    """只核对已交付文件与 CPU 加载，不读取数据源或任何官方测试标签。"""
    manifest = verify_manifest(run_directory, "machineguard.lstm_delivery.v1")
    predictor = LSTMPredictor(run_directory / "deployment")
    last, best = load_checkpoint(run_directory / "last.pt"), load_checkpoint(run_directory / "best.pt")
    history = json.loads((run_directory / "history.json").read_text(encoding="utf-8"))
    if (manifest["completed_epochs"] != 200 or last["epoch"] != 200 or last["kind"] != "last"
            or last["mode"] != "training" or history["completed_epochs"] != 200 or history["status"] != "complete"
            or len(last["history"]) != 200 or history["history"] != last["history"]
            or manifest["model_version"] != last["run_id"] or best["run_id"] != last["run_id"]
            or predictor.manifest["model_version"] != last["run_id"]
            or manifest["best_epoch"] != best["epoch"] or last["best_epoch"] != best["epoch"]
            or predictor.manifest["best_epoch"] != best["epoch"]):
        raise TrainingError("训练完成状态、模型版本或最佳轮数不一致。")
    if manifest["deployment_manifest_sha256"] != file_record(run_directory / "deployment/model_manifest.json")["sha256"]:
        raise TrainingError("部署包清单与离线交付不一致。")
    model_state = predictor.model.state_dict()
    if set(model_state) != set(best["model_state"]) or any(
            not torch.equal(value, best["model_state"][name]) for name, value in model_state.items()):
        raise TrainingError("CPU 部署权重与正式 best 不一致。")
    with ZipFile(run_directory / "source_snapshot.zip") as archive:
        if set(archive.namelist()) != {item["file"] for item in manifest["source_files"]}:
            raise TrainingError("源码归档与清单不一致。")
        for item in manifest["source_files"]:
            content = archive.read(item["file"])
            if len(content) != item["size_bytes"] or sha256(content).hexdigest() != item["sha256"]:
                raise TrainingError("归档源码 SHA-256 校验失败。")
    return {"model_version": manifest["model_version"], "completed_epochs": 200,
            "best_epoch": best["epoch"], "checked_offline_files": len(manifest["files"]),
            "checked_deployment_files": len(predictor.manifest["files"]), "cpu_weights_match_best": True}
