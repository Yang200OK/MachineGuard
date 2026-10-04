"""启动独立 Worker；--once 领取并处理一项任务后退出。"""

import argparse
import logging
import signal
from pathlib import Path
import sys
from threading import Event
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from machineguard.server.database import Database
from machineguard.server.job_config import load_worker_configuration
from machineguard.server.settings import ServerSettings
from machineguard.worker.runner import tick


def main():
    parser = argparse.ArgumentParser(description="MachineGuard 独立任务 Worker")
    parser.add_argument("--once", action="store_true", help="处理一次领取或到期检查后退出")
    parser.add_argument("--worker-id", default="worker-" + uuid4().hex)
    args = parser.parse_args()
    if not 1 <= len(args.worker_id) <= 128 or not all(c.isascii() and (c.isalnum() or c in "_.-") for c in args.worker_id):
        parser.error("worker-id 必须为 1～128 个 ASCII 字母、数字、下划线、点或短横线。")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = Event()
    # 正常停止完成当前任务后退出；进程被强制结束时依赖持久化租约处理。
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stop.set())
    config = load_worker_configuration()
    database = Database(ServerSettings())
    try:
        database.check()
        print(f"Worker 已启动：{args.worker_id}；独立进程；租约 {config.lease_seconds} 秒，心跳 {config.heartbeat_seconds} 秒。", flush=True)
        while not stop.is_set():
            processed = tick(database, config, args.worker_id)
            if args.once:
                print("本次处理完成。" if processed else "本次没有可领取任务；到期检查已完成。", flush=True)
                break
            if not processed:
                stop.wait(config.poll_seconds)
    finally:
        database.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
