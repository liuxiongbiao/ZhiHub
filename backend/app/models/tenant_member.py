"""
backend/app/models/tenant_member.py

职责: tenant_members 表 ORM 模型定义（租户-用户多对多关联，携带角色）
对应日程: 001-account-tenant

核心设计:
  多对多关系中间表，一个用户可加入多个租户，每次加入携带一个角色。
  关键字段: tenant_id、user_id、role(admin/agent)；(tenant_id, user_id) 联合唯一防重复加入。
"""

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


# 租户-用户关联表：多对多关系的中间表，一个用户可加入多个租户，每次加入携带一个角色
class TenantMember(BaseModel):
    __tablename__ = "tenant_members"
    # 同一个用户不能在同一租户下重复加入（联合唯一约束）
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),)

    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    # 该用户在这个租户内的角色：admin(租户管理员)/agent(普通坐席)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
