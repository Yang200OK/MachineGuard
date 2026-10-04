"""迁移、检查和交互式管理员配置，不从命令行传入密码。"""

import argparse
from datetime import datetime, UTC
import getpass
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text, update

from machineguard.server.auth_service import audit
from machineguard.server.database import Database, SCHEMA_REVISION
from machineguard.server.models import Role, SessionRecord, User, UserRole
from machineguard.server.schemas import CreateUserRequest
from machineguard.server.security import PASSWORD_HASHER
from machineguard.server.settings import ServerSettings


def main() -> int:
    parser = argparse.ArgumentParser(description="MachineGuard 数据库管理")
    parser.add_argument("operation", choices=("upgrade", "check", "create-admin", "reset-password"))
    parser.add_argument("--username", default="admin")
    parser.add_argument("--display-name", default="管理员")
    args = parser.parse_args()
    if args.operation == "upgrade":
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        print(f"迁移已完成：{SCHEMA_REVISION}。")
        return 0
    database = Database(ServerSettings())
    try:
        database.check()
        if args.operation == "check":
            with database.engine.connect() as connection:
                version = connection.execute(text("SELECT version()")).scalar_one()
                roles = connection.execute(text("SELECT name FROM roles ORDER BY name")).scalars().all()
            print(f"PostgreSQL 连接与迁移：通过；{version}")
            print(f"角色：{', '.join(roles)}")
            return 0
        password = getpass.getpass("请输入密码（至少 12 字符，输入不回显）：")
        confirmation = getpass.getpass("请再次输入密码：")
        if password != confirmation:
            raise ValueError("两次密码不一致。")
        payload = CreateUserRequest(username=args.username, display_name=args.display_name,
                                    password=password, roles=["admin"])
        with database.sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(774413001)"))
            user = session.scalar(select(User).where(User.username == payload.username))
            if args.operation == "create-admin":
                if session.scalar(select(UserRole.user_id).where(UserRole.role_name == "admin")) is not None or user is not None:
                    raise ValueError("已有管理员或同名账号；不能再次初始化。需要重置时使用 reset-password。")
                user = User(username=payload.username, display_name=payload.display_name,
                            password_hash=PASSWORD_HASHER.hash(password), roles=[session.get(Role, "admin")])
                session.add(user)
                session.flush()
                action = "user.admin_bootstrap"
            else:
                if user is None or user.is_guest:
                    raise ValueError("需要重置的正式账号不存在。")
                user.password_hash = PASSWORD_HASHER.hash(password)
                session.execute(update(SessionRecord).where(SessionRecord.user_id == user.id,
                                                            SessionRecord.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
                action = "user.password_reset_cli"
            audit(session, None, action, "user", user.id, uuid4(), {"username": user.username})
        print(f"账号操作已完成：{payload.username}；未显示密码。")
    finally:
        database.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
