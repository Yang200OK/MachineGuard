"""原始数据检查命令，不执行训练或在线调用。"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import ConfigurationError, load_data_configuration
from .data import DatasetError, load_fd001
from .paths import get_project_root


def main() -> int:
    """读取、校验指定划分，并在全部检查通过后保存摘要。"""
    project_root = get_project_root()
    parser = argparse.ArgumentParser(description="MachineGuard FD001 原始数据检查")
    parser.add_argument("--split", choices=("train", "test"), required=True)
    parser.add_argument(
        "--config", type=Path, default=project_root / "configs" / "data.json",
        help="UTF-8 JSON 数据配置路径",
    )
    parser.add_argument("--data-dir", type=Path, help="显式指定原始数据目录")
    parser.add_argument("--summary", type=Path, help="显式指定摘要输出路径")
    args = parser.parse_args()
    try:
        configuration = load_data_configuration(args.config)
        source_directory = (
            args.data_dir.resolve()
            if args.data_dir is not None
            else configuration.source_directory
        )
        dataset = load_fd001(source_directory, args.split)
        summary = dataset.summarize()
        summary["schema_version"] = "machineguard.raw_dataset_summary.v1"
        summary["created_at"] = datetime.now(UTC).isoformat()
        summary["execution"] = {
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "project_root": str(project_root),
            "reader_module": str(Path(__file__).resolve().parent / "data" / "cmapss.py"),
        }
        summary_path = (
            args.summary.resolve()
            if args.summary is not None
            else configuration.summary_directory / f"fd001_{args.split}_summary.json"
        )
        # 摘要仅写 JSON，避免命令参数误将原始 txt 文件作为输出目标。
        if summary_path.suffix.lower() != ".json":
            raise ConfigurationError("摘要输出路径必须以 .json 结尾，不能覆盖原始 txt 数据。")
        content = json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(content + "\n", encoding="utf-8")
    except (ConfigurationError, DatasetError, OSError) as error:
        parser.exit(status=1, message=f"数据检查失败：{error}\n")

    print(f"项目目录：{project_root}")
    print(f"Python：{sys.executable}")
    print(f"数据：{dataset.dataset} / {dataset.split}（NASA 模拟数据）")
    print(f"行数：{summary['row_count']}；发动机：{summary['engine_count']}；字段：26")
    print(f"周期范围：{summary['cycle_range']['min']} ~ {summary['cycle_range']['max']}")
    print("完整性检查：通过（有限数值、唯一设备周期、周期从 1 开始且连续）")
    print(f"源文件 SHA-256：{dataset.source_sha256}")
    print(f"摘要：{summary_path}")
    return 0
