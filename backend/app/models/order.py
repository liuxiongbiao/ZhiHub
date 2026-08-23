"""
backend/app/models/order.py

职责: orders 表 ORM 模型定义（订单，含乐观锁 version 字段）

核心设计:
  资金类数据，状态机 + 乐观锁防并发覆写：
    created -> paid -> shipped -> completed；created -> cancelled；
    paid/shipped -> refund_requested -> refunding -> refunded
  关键字段: tenant_id(隔离键)、order_no、amount/paid_amount、status、version(乐观锁版本号)、phone(规格 006 新增，AES-GCM 加密)
"""

from decimal import Decimal

from sqlalchemy import BigInteger, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.models.base import BaseModel

# 合法状态跃迁 Map（终态集合为空，不可再跃迁）
VALID_TRANSITIONS: dict[str, set[str]] = {
    "created": {"paid", "cancelled"},
    "paid": {"shipped", "refund_requested"},
    "shipped": {"completed", "refund_requested"},
    "refund_requested": {"refunding", "paid", "shipped"},
    "refunding": {"refunded", "paid", "shipped"},  # 兼容上线前已进入 refunding 的存量申请
    # 终态不可跃迁
    "completed": set(),
    "refunded": set(),
    "cancelled": set(),
}


class Order(BaseModel):
    __tablename__ = "orders"

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    order_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    # T-遗留1: 资金用 Numeric(10,2) 防浮点精度（金额相等比较/累加误差）
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    # 乐观锁版本号: 每次 UPDATE WHERE version={expected}，冲突则 rowcount=0
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 规格 006 新增：终端用户绑定手机号，查询订单需校验 phone 后4位匹配（双因子校验）
    # AES-GCM 加密存储（PRD §3.1/4.3 PII 静态加密）；密文不确定故不加 index
    phone: Mapped[str | None] = mapped_column(EncryptedString(255), nullable=True)
    # 订单自身的支付交易号：退款必须走订单自己的支付流水，防退错账。
    # demo 无真实支付流程恒为 NULL；真实接入后订单支付完成时回填本字段，
    # _invoke_alipay_refund 优先用它退款，无流水时降级为租户最近账单（打 warning）。
    payment_trade_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 规格 013 US6: 物流单号，发货（paid→shipped）时写入（PRD §3.6）
    tracking_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # PRD §3.5 界面要求：区分 Agent 创建 vs 坐席/后台创建（agent/staff）
    created_by: Mapped[str] = mapped_column(String(20), nullable=False, default="staff")
