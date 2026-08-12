"""
backend/app/crud/tenant.py

职责: tenants / tenant_members 表的数据库访问层（租户与成员关联存取）

核心设计:
  纯数据存取无业务判断，只 flush 不 commit，事务由 auth_service.register() 统一 commit/rollback。
  get_member 按 tenant_id + user_id 联合查询，登录时取该用户在租户内的角色。
"""

from sqlalchemy.orm import Session

from app.models.tenant import Tenant
from app.models.tenant_member import TenantMember


def get_by_widget_key_hash(db: Session, digest: str) -> Tenant | None:
    """按 Widget API Key 的 SHA-256 哈希查找租户（014，仅存哈希不存明文）。"""
    from sqlalchemy import select
    return db.execute(
        select(Tenant).where(Tenant.widget_api_key_hash == digest)
    ).scalar_one_or_none()


def create_tenant(
    db: Session,
    *,
    name: str,
    plan_type: str = "trial",
    seat_limit: int = 1,
    status: str = "active",
) -> Tenant:
    """创建租户，flush 拿 id，不 commit。

    plan_type/status 可选: 默认 trial/active；企业版注册传 plan_type="enterprise",
    status="pending_review"（待人工审批，PRD 3.1 #17）。
    """
    tenant = Tenant(
        name=name, plan_type=plan_type, seat_limit=seat_limit, status=status
    )
    db.add(tenant)
    db.flush()
    return tenant


def add_member(db: Session, *, tenant_id: int, user_id: int, role: str) -> TenantMember:
    """新增租户成员关联（注册时绑 admin 角色用）。"""
    member = TenantMember(tenant_id=tenant_id, user_id=user_id, role=role)
    db.add(member)
    db.flush()
    return member


def get_by_id(db: Session, tenant_id: int) -> Tenant | None:
    """按主键查询租户（deps.require_tenant 用）。"""
    return db.get(Tenant, tenant_id)


def get_member(db: Session, *, tenant_id: int, user_id: int) -> TenantMember | None:
    """按 tenant_id + user_id 查询成员关联（登录时取该用户在租户内的角色）。"""
    from sqlalchemy import select

    return db.execute(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id
        )
    ).scalar_one_or_none()


def count_members(db: Session, tenant_id: int) -> int:
    """统计租户成员数（seat_limit 校验用）。"""
    from sqlalchemy import func, select

    return db.execute(
        select(func.count()).select_from(TenantMember).where(
            TenantMember.tenant_id == tenant_id
        )
    ).scalar_one()


def list_members(db: Session, tenant_id: int) -> list[TenantMember]:
    """列出租户全部成员（按 id 升序，seat 管理与成员展示用）。"""
    from sqlalchemy import select

    return list(
        db.execute(
            select(TenantMember).where(TenantMember.tenant_id == tenant_id).order_by(TenantMember.id)
        ).scalars().all()
    )


def update_widget(
    db: Session,
    tenant: Tenant,
    *,
    theme_color: str | None = None,
    logo_url: str | None = None,
    welcome_text: str | None = None,
) -> Tenant:
    """更新租户 Widget 品牌配置（020 PRD 3.8）。

    仅更新非 None 字段（增量更新），flush 不 commit。
    """
    if theme_color is not None:
        tenant.widget_theme_color = theme_color
    if logo_url is not None:
        tenant.widget_logo_url = logo_url
    if welcome_text is not None:
        tenant.widget_welcome_text = welcome_text
    db.flush()
    return tenant
