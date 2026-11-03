"""
backend/app/core/database.py

职责: 同步 SQLAlchemy 引擎、SessionLocal 工厂、get_db() 依赖生成器、init_db 建表兜底

核心设计:
  同步 SQLAlchemy 2.0 + pymysql（匹配 requirements.txt 同步驱动，避免 async 复杂度）。
  每请求一 Session，由 FastAPI Depends(get_db) 注入，事务边界在 Service 层显式控制。
  建表单一迁移轨: alembic_version 表存在（迁移已接管）则 init_db 跳过 create_all；
  不存在则仅 demo 兜底建表并告警提示先跑 alembic upgrade head。

关键技术点:
  - pool_pre_ping=True: 从连接池取连接前先发 ping，剔除 MySQL 断开的陈旧连接。
    MySQL 默认 wait_timeout 8h，连接池中长时间空闲的连接可能已被服务端断开，
    不 pre_ping 会报 "MySQL server has gone away"。
  - autoflush=False: Session 查询前不自动 flush，避免隐式 SQL 执行，
    让开发者显式控制 flush 时机（减少意外 SQL、方便调试）。
  - future=True: 启用 SQLAlchemy 2.0 风格 API（2.0 style），
    与新版本行为一致，避免 legacy 1.x 语法废弃。
  - 单一迁移轨: 检测 alembic_version 表决定是否 create_all，避免双轨并行
    （create_all 建表后 alembic 迁移会因 Table already exists 卡死，且掩盖漏写迁移的缺口）。

流程:
  1. 应用启动 -> create_engine() 建立连接池（pool_pre_ping=True 探活）
  2. 每次请求 -> Depends(get_db) -> SessionLocal() 创建新 Session
  3. Service 层使用该 Session 执行 crud + commit/rollback
  4. 请求结束 -> finally db.close() 归还连接到池
  5. 测试环境通过依赖覆写（dependency_overrides）替换为 SQLite in-memory

依赖关系:
  - 上游: app/api/*（Depends(get_db) 注入）、app/main.py startup（init_db）
  - 下游: MySQL（通过 pymysql 驱动）、app.models.Base（metadata.create_all）
"""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


def _build_mysql_url() -> str:
    """构造 MySQL 连接 URL（pymysql 驱动 + utf8mb4 字符集）。"""
    return (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"
        "?charset=utf8mb4"
    )


# pool_pre_ping：连接取出前先探活，避免 MySQL 断开的陈旧连接导致请求失败
# future=True：启用 SQLAlchemy 2.0 风格 API
engine = create_engine(_build_mysql_url(), pool_pre_ping=True, future=True)

# autoflush=False：查询前不自动 flush，显式控制 SQL 执行时机
# autocommit=False：事务需显式 commit，防止隐式提交导致的数据不一致
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求创建一个 Session，请求结束后关闭。

    生成器模式: yield db 提供 Session 给路由，finally 确保连接回收。
    事务的 commit/rollback 由调用方（Service 层）负责，本生成器只保证连接回收（close）。
    close 只是把连接归还连接池，不是 rollback——未 commit 的事务在 close 时会被
    SQLAlchemy 自动 rollback（autocommit=False 模式下）。

    Yields:
        Session: SQLAlchemy ORM Session 实例
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表入口：alembic 已接管（alembic_version 表存在）则跳过，否则 demo 兜底 create_all。

    单一迁移轨: alembic_version 存在说明库由 alembic 管理，create_all 不再掺和
    （避免双轨并行：create_all 建出的表会让后续迁移 Table already exists 卡死，
    且掩盖"模型新增表但漏写迁移"的缺口）。
    无 alembic_version 的库（demo 直接起服务）create_all 兜底建表，并告警提示
    正确流程是先 alembic upgrade head。

    延迟 import Base: 避免循环依赖（app.models 可能间接 import database），
    确保 init_db 调用时所有模型已注册到 Base.metadata。
    """
    from app.models import Base  # 触发所有模型注册到 Base.metadata

    if "alembic_version" in inspect(engine).get_table_names():
        logger.info("Alembic 已管理 schema，跳过 create_all（单一迁移轨）")
        return
    if settings.app_env == "production":
        raise RuntimeError("生产环境必须先执行 alembic upgrade head，禁止 create_all 兜底建表")
    logger.warning("未检测到 alembic_version 表，由 create_all 兜底建表；生产环境请先执行 alembic upgrade head")
    Base.metadata.create_all(bind=engine)
