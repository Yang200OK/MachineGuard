"""显式选择快速核对或正式训练，错误保持可见。"""

import argparse
from pathlib import Path
import sys

from .config import ConfigurationError
from .data.artifacts import load_training_data
from .data.cmapss import DatasetError
from .paths import get_project_root
from .training import TrainingError
from .training_config import load_training_configuration
from .training_runner import run_training


def main() -> int:
    root = get_project_root()
    parser = argparse.ArgumentParser(description="MachineGuard LSTM 训练与恢复")
    parser.add_argument("--config", type=Path, default=root / "configs/training.json")
    parser.add_argument("--data-dir", type=Path, default=root / "data/processed")
    parser.add_argument("--artifact-root", type=Path, default=root / "artifacts")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--smoke", action="store_true", help="各 65 个窗口、2 轮，仅核对入口")
    parser.add_argument("--resume", type=Path, help="原运行目录的 last.pt")
    parser.add_argument("--stop-after-epoch", type=int, help="完成指定累计轮数后暂停，不改变最终目标")
    args = parser.parse_args()
    try:
        config = load_training_configuration(args.config)
        data = load_training_data(args.data_dir)
        print(f"项目目录：{root}\nPython：{sys.executable}")
        run_training(data, config, args.artifact_root, args.device, args.smoke, args.resume, args.stop_after_epoch)
    except (ConfigurationError, DatasetError, TrainingError, OSError, RuntimeError) as error:
        print(f"训练失败：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("训练已中断。使用最后完整轮的 last.pt 恢复；未完成的当前轮将重新执行。", file=sys.stderr)
        return 130
    return 0
