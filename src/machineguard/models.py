"""单向 LSTM 读取窗口最后时间步进行原始 RUL 回归。"""

import torch
from torch import nn


class LSTMRULRegressor(nn.Module):
    """输入 [批量, 时间步, 特征]，输出 [批量]。"""

    def __init__(self, input_size: int, hidden_size: int, num_layers: int, dropout: float):
        super().__init__()
        self.input_size = input_size
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers=num_layers,
                            dropout=dropout, batch_first=True, bidirectional=False)
        self.regression = nn.Linear(hidden_size, 1)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if inputs.ndim != 3 or inputs.shape[0] == 0 or inputs.shape[1] == 0 or inputs.shape[2] != self.input_size:
            raise ValueError("LSTM 输入必须是非空 [批量, 时间步, 有序特征数]。")
        if inputs.dtype != torch.float32 or not torch.isfinite(inputs).all():
            raise ValueError("LSTM 输入必须为有限 FP32。")
        sequence, _ = self.lstm(inputs)
        # 只移除回归输出维，批量为 1 时仍返回 [1]。
        return self.regression(sequence[:, -1, :]).squeeze(-1)
