"""从当前项目源码启动 LSTM，不修改旧项目环境安装。"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from machineguard.train_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
