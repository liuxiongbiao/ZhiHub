"""
backend/alembic/env.py
职责: Alembic环境配置——数据库连接（从config.py读取）、自动发现ORM模型Metadata
分工: 🟢 弱模型可独立完成（标准模板）
对应日程: Day 1
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 把 backend/ 目录加入 sys.path，这样下面才能 import app.xxx
# （alembic 是从 backend/ 目录运行的，但它本身不会自动把项目根目录加入路径）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.models import Base  # 触发 app/models/__init__.py，让所有模型注册到 Base.metadata

config = context.config

# 读取 alembic.ini 里的 [loggers] 配置，初始化日志格式
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 不用 alembic.ini 里写死的 sqlalchemy.url，而是从项目统一的 Settings（环境变量）拼接连接串，
# 这样数据库账号密码只需要在一处（.env / config.py）维护
mysql_url = (
    f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
    f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"
)
config.set_main_option("sqlalchemy.url", mysql_url)

# target_metadata 就是 autogenerate 用来"对比数据库当前结构 vs 代码里定义的模型"的依据
target_metadata = Base.metadata


# 离线模式：不连接真实数据库，只生成 SQL 脚本（这里没用到，但alembic模板标配）
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# 在线模式：真正连接数据库执行迁移（我们用的是这个，NullPool表示迁移用完连接就关，不做连接池复用）
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
