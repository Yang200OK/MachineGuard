"""从当前源码启动 CPU 原始窗口预测。"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from machineguard.predict_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
