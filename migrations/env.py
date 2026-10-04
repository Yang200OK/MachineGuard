"""只从本地凭据读取迁移账号，不将连接密码写入 Alembic 配置。"""

from alembic import context

from machineguard.server.database import make_engine
from machineguard.server.models import Base
from machineguard.server import business_models
from machineguard.server import job_models
from machineguard.server import document_models
from machineguard.server import parse_models
from machineguard.server import knowledge_models
from machineguard.server import index_models
from machineguard.server import qa_models
from machineguard.server import agent_models
from machineguard.server import recovery_models
from machineguard.server import report_models
from machineguard.server import delivery_models
from machineguard.server import graph_models
from machineguard.server import mcp_models
from machineguard.server.settings import ServerSettings


settings = ServerSettings()
url = settings.migration_database_url.get_secret_value()
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
