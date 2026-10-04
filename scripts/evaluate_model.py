"""从当前源码启动正式模型的离线评估与导出。"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from machineguard.evaluate_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
