"""SQLAlchemy 连接与事务，运行账号不拥有结构迁移权限。"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .settings import ServerSettings


SCHEMA_REVISION = "0017_mcp_access"


def make_engine(url: str):
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_timeout=5,
                         hide_parameters=True, connect_args={"connect_timeout": 5, "application_name": "machineguard"})


class Database:
    def __init__(self, settings: ServerSettings):
        self.engine = make_engine(settings.database_url.get_secret_value())
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def check(self) -> None:
        """启动前必须完成迁移；不自动 create_all 或跳过失败。"""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if revisions != [SCHEMA_REVISION]:
                raise RuntimeError("数据库迁移版本不一致，请先执行迁移。")
