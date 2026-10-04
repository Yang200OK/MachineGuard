"""分离训练与验证，按真实样本数汇总回归指标。"""

import math

import torch
from torch import nn
from torch.utils.data import DataLoader


class TrainingError(ValueError):
    """训练契约或数值状态非法，不能继续产生成功产物。"""


def run_epoch(model: nn.Module, loader: DataLoader, device: torch.device,
              optimizer: torch.optim.Optimizer | None = None, gradient_clip: float = 5.0) -> dict:
    """训练指标来自更新过程，验证指标来自固定轮末权重。"""
    training = optimizer is not None
    model.train(training)
    squared_sum, absolute_sum, count = 0.0, 0.0, 0
    with torch.enable_grad() if training else torch.inference_mode():
        for inputs, targets in loader:
            inputs, targets = inputs.to(device), targets.to(device)
            if targets.ndim != 1 or not torch.isfinite(targets).all():
                raise TrainingError("回归标签形状非法或存在 NaN/Inf。")
            if training:
                optimizer.zero_grad(set_to_none=True)
            predictions = model(inputs)
            if predictions.shape != targets.shape or not torch.isfinite(predictions).all():
                raise TrainingError("回归预测与标签未对齐或存在 NaN/Inf。")
            loss = nn.functional.mse_loss(predictions, targets)
            if not torch.isfinite(loss):
                raise TrainingError("MSE 非有限，停止训练。")
            if training:
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), gradient_clip, error_if_nonfinite=True)
                optimizer.step()
            # 使用双精度累加误差，每个样本权重相同，包括最后不足一批的样本。
            differences = predictions.detach().double() - targets.double()
            squared_sum += differences.square().sum().item()
            absolute_sum += differences.abs().sum().item()
            count += len(targets)
    if count == 0:
        raise TrainingError("该轮没有样本。")
    mse = squared_sum / count
    return {"samples": count, "mse": mse, "rmse": math.sqrt(mse), "mae": absolute_sum / count}
