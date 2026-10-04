"""固定正式模型的 CPU 评估与模型交付入口。"""

import argparse
from pathlib import Path
import sys

from .config import ConfigurationError, load_data_configuration
from .data.artifacts import load_training_data
from .data.cmapss import DatasetError
from .evaluation import evaluate_and_export, verify_delivery
from .paths import get_project_root
from .training import TrainingError


def main() -> int:
    root = get_project_root()
    parser = argparse.ArgumentParser(description="完整 200 轮 LSTM 评估与交付")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=root / "data/processed")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true", help="只核对交付清单和 CPU 加载，不重新评估或读取标签")
    args = parser.parse_args()
    try:
        if args.verify_only:
            result = verify_delivery(args.run_dir.resolve())
            print(f"SHA-256 核对：通过；离线文件 {result['checked_offline_files']}；部署文件 {result['checked_deployment_files']}")
            print(f"模型：{result['model_version']}；累计 {result['completed_epochs']} 轮；best 第 {result['best_epoch']} 轮")
            print("CPU 部署权重与 best 一致；未读取官方测试标签。")
            return 0
        data_config = load_data_configuration(root / "configs/data.json")
        data = load_training_data(args.data_dir)
        metrics = evaluate_and_export(args.run_dir.resolve(), data,
                                      args.source_dir if args.source_dir is not None else data_config.source_directory, root)
        print(f"累计训练：200 轮；best：第 {metrics['best_epoch']} 轮；CPU 评估")
        for label, result in (("验证", metrics["validation"]), ("官方测试末端", metrics["official_test_terminal"])):
            raw = result["raw"]
            print(f"{label}：{raw['samples']} 个样本；原始 MAE {raw['mae']:.6f}；RMSE {raw['rmse']:.6f}")
        for label, result in (("验证", metrics["ridge_baseline"]["validation"]),
                              ("官方测试末端", metrics["ridge_baseline"]["official_test_terminal"])):
            print(f"Ridge 离线{label}：MAE {result['raw']['mae']:.6f}；RMSE {result['raw']['rmse']:.6f}")
        print(f"曲线、评估与 SHA-256 清单：{args.run_dir.resolve()}")
        print(f"CPU 部署包：{args.run_dir.resolve() / 'deployment'}")
    except (ConfigurationError, DatasetError, TrainingError, OSError, RuntimeError, UnicodeError) as error:
        print(f"评估失败：{error}", file=sys.stderr)
        return 1
    return 0
