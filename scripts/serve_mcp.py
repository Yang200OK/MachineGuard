"""本地 MCP stdio 入口：环境凭据只用于受控服务，stdout 只输出协议。"""

from pathlib import Path
import logging
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import anyio
from mcp.server.stdio import stdio_server
from machineguard.mcp.server import create_mcp_server
from machineguard.server.database import Database
from machineguard.server.settings import ServerSettings
from machineguard.server.device_service import DeviceRuntime
from machineguard.server.business_config import load_business_configuration
from machineguard.server.skill_loader import SkillRegistry
from machineguard.server.mcp_config import load_mcp_configuration
from machineguard.server.mcp_service import McpRuntime


async def main():
    token = os.environ.get("MACHINEGUARD_MCP_TOKEN", "")
    database = Database(ServerSettings())
    try:
        database.check()
        runtime = DeviceRuntime(load_business_configuration())
        with database.sessions() as session:
            runtime.check_registration(session)
        service = McpRuntime(database, runtime, SkillRegistry(), load_mcp_configuration())
        service.verify(token)
        server = create_mcp_server(service, stdio_token=token)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")
    try:
        anyio.run(main)
    except Exception as error:
        print(f"MCP 启动或运行失败（{type(error).__name__}），请检查独立凭据、数据库及服务日志。", file=sys.stderr)
        raise SystemExit(1) from None
