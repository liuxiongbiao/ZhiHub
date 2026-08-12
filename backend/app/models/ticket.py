"""
backend/app/models/ticket.py

职责: tickets 表 ORM 模型定义（工单）

核心设计:
  工单流转载体，状态机 open -> in_progress -> resolved -> closed，支持坐席认领。
  关键字段: tenant_id(隔离键)、ticket_no(业务编号 TK-YYYY-NNN)、status、priority、assignee_id、
            phone(006 新增，双因子校验；AES-GCM 加密存储 PRD 3.1/4.3)、category(007 新增，建单分类)
"""

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.models.base import BaseModel


class Ticket(BaseModel):
    __tablename__ = "tickets"

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # 工单编号: TK-YYYY-NNN
    ticket_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 状态: open / in_progress / resolved / closed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    # 优先级: low / medium / high / urgent
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    # 指派人 ID（007-session-lifecycle 启用坐席认领）
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", name="fk_tickets_assignee_id"), nullable=True)  # T27 FK
    # 006-mcp-security 新增：终端用户绑定手机号，查询工单需校验 phone 后4位匹配（双因子校验）
    # AES-GCM 加密存储（PRD 3.1/4.3 PII 静态加密）；密文不确定故不加 index
    phone: Mapped[str | None] = mapped_column(EncryptedString(255), nullable=True)
    # 007-session-lifecycle 新增：工单分类（general 默认 / message 留言建单 / 未来扩展 complaint 等）
    category: Mapped[str | None] = mapped_column(String(30), nullable=True, default="general")
