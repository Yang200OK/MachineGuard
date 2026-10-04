"""仅监听本机启动 API。"""

from pathlib import Path
import logging
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
from machineguard.server.app import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动本机 MachineGuard API")
    parser.add_argument("--port", type=int, default=8000, choices=range(1, 65536), metavar="PORT")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=True)
