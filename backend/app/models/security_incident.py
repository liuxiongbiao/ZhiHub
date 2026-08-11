"""
backend/app/models/security_incident.py

职责: security_incidents 表 ORM 模型定义（Prompt 注入拦截记录）
对应日程: 006-mcp-security

核心设计:
  命中注入规则的事件落库，供事后审查与规则优化；用户输入脱敏后截断存储。
  关键字段: tenant_id(隔离键)、session_id(关联会话)、user_message_preview(脱敏摘要)、
            rule_hit(命中规则名)、risk_level(风险等级)、action_taken(处置动作)
"""
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class SecurityIncident(BaseModel):
    __tablename__ = "security_incidents"

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)  # 租户隔离键
    session_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sessions.id", name="fk_security_incidents_session_id"), nullable=True)  # 关联会话（可空，T27 FK）
    # 触发规则的用户输入摘要（脱敏后截断 500 字）
    user_message_preview: Mapped[str] = mapped_column(String(500), nullable=False)
    # 命中的规则名，如 ignore_instruction / role_hijack
    rule_hit: Mapped[str] = mapped_column(String(100), nullable=False)
    # low / medium / high
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    # blocked / transferred_to_human
    action_taken: Mapped[str] = mapped_column(String(20), nullable=False)
