"""生成训练/验证窗口与可追溯的预处理产物。"""

import argparse
from pathlib import Path
import sys

from .config import ConfigurationError, load_data_configuration, load_preparation_configuration
from .data import DatasetError, load_fd001
from .data.preparation import prepare_fd001, save_prepared_dataset
from .paths import get_project_root


def main() -> int:
    """使用原始训练文件准备数据，不训练模型，不读取官方测试标签。"""
    project_root = get_project_root()
    parser = argparse.ArgumentParser(description="MachineGuard FD001 训练/验证数据准备")
    parser.add_argument("--data-config", type=Path, default=project_root / "configs/data.json")
    parser.add_argument("--config", type=Path, default=project_root / "configs/preparation.json")
    parser.add_argument("--data-dir", type=Path, help="显式指定原始数据目录")
    parser.add_argument("--output-dir", type=Path, help="显式指定处理产物目录")
    args = parser.parse_args()
    try:
        data_configuration = load_data_configuration(args.data_config)
        configuration = load_preparation_configuration(args.config)
        source_directory = (
            args.data_dir.resolve() if args.data_dir is not None
            else data_configuration.source_directory
        )
        output_directory = (
            args.output_dir.resolve() if args.output_dir is not None
            else data_configuration.summary_directory
        )
        prepared = prepare_fd001(load_fd001(source_directory, "train"), configuration)
        arrays_path, summary_path = save_prepared_dataset(prepared, output_directory)
    except (ConfigurationError, DatasetError, OSError) as error:
        parser.exit(status=1, message=f"数据准备失败：{error}\n")
    print(f"项目目录：{project_root}")
    print(f"Python：{sys.executable}")
    print(f"训练发动机：{len(prepared.train_engine_ids)}；验证发动机：{len(prepared.validation_engine_ids)}；重叠：0")
    print(f"训练原始行：{prepared.train_row_count}；验证原始行：{prepared.validation_row_count}")
    print(f"删除常量列：{', '.join(prepared.constant_columns)}")
    print(f"有序特征（{len(prepared.feature_columns)}）：{', '.join(prepared.feature_columns)}")
    print(f"scaler 拟合行数：{int(prepared.scaler.n_samples_seen_)}（仅训练原始行）")
    print(f"训练窗口：{list(prepared.train.features.shape)}；标签：{list(prepared.train.targets.shape)}")
    print(f"验证窗口：{list(prepared.validation.features.shape)}；标签：{list(prepared.validation.targets.shape)}")
    print(f"离线跳过短轨迹：训练 {len(prepared.train.skipped_engines)} 台，验证 {len(prepared.validation.skipped_engines)} 台")
    print(f"首个训练窗口：设备 {int(prepared.train.engine_ids[0])}，周期 {int(prepared.train.start_cycles[0])}～{int(prepared.train.end_cycles[0])}，RUL {float(prepared.train.targets[0])}")
    print(f"窗口数组：{arrays_path}")
    print(f"处理摘要：{summary_path}")
    print("处理完成；当前未启动模型训练，未使用官方测试标签。")
    return 0
