"""从当前源码运行数据检查，不依赖旧项目的 editable 安装。"""

import sys
from pathlib import Path


# 显式将本项目 src 放在导入路径首位，不修改 work1 的全局安装。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from machineguard.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
