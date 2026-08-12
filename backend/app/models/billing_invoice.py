"""
backend/app/models/billing_invoice.py

职责: billing_invoices 表 ORM 模型定义（月度账单）

核心设计:
  按月生成的账单，base_fee 套餐基础费 + overage_fee 超量费，支付成功后回填交易号与支付时间。
  关键字段: tenant_id(隔离键)、period(账期 YYYY-MM)、base_fee、overage_fee、status(unpaid/paid)、
            payment_trade_no(支付交易号，唯一)、paid_at
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import BaseModel

class BillingInvoice(BaseModel):
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "period", name="uq_invoice_tenant_period"),)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # 账期，如 "2026-08"
    base_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0.0)  # 套餐基础费（Numeric 防精度）
    overage_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0.0)  # 超量费（Numeric 防精度）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unpaid")  # unpaid / paid
    payment_trade_no: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)  # 支付交易号
    paid_at: Mapped[datetime | None] = mapped_column(nullable=True)  # 支付完成时间
