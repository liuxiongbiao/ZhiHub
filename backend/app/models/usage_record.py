"""
backend/app/models/usage_record.py

职责: usage_records 表 ORM 模型定义（按天聚合用量）

核心设计:
  按租户+日期聚合的对话用量，is_overage 标记是否超出套餐配额，账单据此计算超量费。
  关键字段: tenant_id(隔离键)、date(统计日期)、conversation_count(当日对话数)、is_overage(是否超量)
"""
from datetime import date as DateType
from sqlalchemy import BigInteger, Date, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import BaseModel

class UsageRecord(BaseModel):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("tenant_id", "date", name="uq_usage_tenant_date"),)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)  # 租户隔离键
    date: Mapped[DateType] = mapped_column(Date, nullable=False)  # 统计日期
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 当日对话数
    is_overage: Mapped[bool] = mapped_column(default=False)  # 是否超出套餐配额
