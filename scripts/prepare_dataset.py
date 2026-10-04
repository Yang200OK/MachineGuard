"""从本项目源码启动训练/验证数据准备。"""

import sys
from pathlib import Path


# 与原始检查入口保持相同的导入方式，不修改旧项目的环境安装。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from machineguard.prepare_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
