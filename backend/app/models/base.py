"""
backend/app/models/base.py

职责: ORM 基类定义，提供公共字段 id/created_at/updated_at
对应日程: 001-account-tenant

核心设计:
  Base 为 DeclarativeBase 根类，alembic 通过 Base.metadata 收集全部表结构做 autogenerate；
  BaseModel 为抽象基类（__abstract__ 不单独建表），业务表继承后自动获得三个公共字段。
  关键字段: id(BigInteger 主键，SQLite 降级 Integer 才能触发自增)、created_at、updated_at
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# 所有 ORM 模型的根基类，alembic 的 env.py 通过 Base.metadata 收集全部表结构
class Base(DeclarativeBase):
    pass


# 抽象基类（__abstract__ = True 表示不会单独建表），封装所有业务表都要有的公共字段
# 各业务模型（User/Tenant/...）继承它即可自动获得 id/created_at/updated_at
class BaseModel(Base):
    __abstract__ = True

    # 主键：生产库（MySQL）用 BigInteger 预留增长空间；SQLite（测试）用 Integer
    # 才能触发自增（SQLite 仅对 INTEGER PRIMARY KEY 自增，不认 BIGINT）
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    # 创建时间：server_default=func.now() 表示由数据库（而非应用层）在插入时自动填充
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    # 更新时间：onupdate=func.now() 表示每次 UPDATE 时数据库自动刷新该字段
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
