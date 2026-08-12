"""
backend/app/models/audit_log.py

职责: audit_logs 表 ORM 模型定义（系统审计日志，PRD 2.1）

核心设计:
  关键业务操作（退款审批/撤销、登录/登出、向导推进、支付回调）落库审计，
  供管理员事后审查与合规追溯。每条记录绑定 tenant_id 实现租户隔离，
  端点查询强制 WHERE tenant_id=? 防止跨租户泄露。

  关键字段: tenant_id(隔离键)、user_id(操作人)、action(操作类型)、
            target_type/target_id(操作对象)、detail(附加描述)

审计写入策略:
  log_audit 只 add+flush 不 commit，由各 service 的 commit 统一提交，
  保证"操作成功才有审计记录"的原子性（操作回滚则审计回滚）。
  登录/登出无其他 DB 写入，需显式 commit 落审计记录。
"""

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AuditLog(BaseModel):
    __tablename__ = "audit_logs"

    # 租户隔离键 -- 端点查询强制 WHERE tenant_id=?，防跨租户泄露
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # 操作人用户 ID（可空：系统自动操作无操作人）
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", name="fk_audit_logs_user_id"), nullable=True, index=True)  # T27 FK
    # 操作类型: refund_approve / refund_cancel / login / logout / wizard_advance / payment
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 操作对象类型: refund_approval / user / tenant / billing_invoice
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 操作对象 ID（字符串兼容 int/非 int 标识）
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 附加描述（JSON 字符串或纯文本，不含 PII）
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
