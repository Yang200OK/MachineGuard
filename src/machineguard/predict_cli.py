"""本地原始窗口 CPU 推理，不读取 RUL_FD001.txt。"""

import argparse
import json
from pathlib import Path
import sys

import torch

from .config import ConfigurationError, load_data_configuration
from .data.cmapss import DatasetError, load_fd001
from .inference import LSTMPredictor
from .paths import get_project_root
from .training import TrainingError


def main() -> int:
    parser = argparse.ArgumentParser(description="MachineGuard 独立 CPU 推理")
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--engine-id", type=int, required=True)
    parser.add_argument("--end-cycle", type=int, required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--split", choices=("train", "test"), default="test")
    args = parser.parse_args()
    try:
        torch.set_num_threads(4)
        config = load_data_configuration(get_project_root() / "configs/data.json")
        source = load_fd001(args.data_dir if args.data_dir is not None else config.source_directory, args.split)
        predictor = LSTMPredictor(args.bundle_dir)
        window_size = predictor.preprocessing["configuration"]["window_size"]
        records = source.records.loc[source.records["engine_id"].eq(args.engine_id)
                                     & source.records["cycle"].between(args.end_cycle - window_size + 1, args.end_cycle)]
        print(json.dumps(predictor.predict(records, args.engine_id, args.end_cycle), ensure_ascii=False, allow_nan=False, indent=2))
    except (ConfigurationError, DatasetError, TrainingError, OSError, RuntimeError) as error:
        print(f"推理失败：{error}", file=sys.stderr)
        return 1
    return 0
