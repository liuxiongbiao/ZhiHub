"""运营看板 API：租户管理员视图与双重鉴权的平台运营视图。"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import settings
from app.services.analytics_service import get_knowledge_quality, get_overview

router = APIRouter()


def _tenant_id(request: Request) -> int:
    value = getattr(request.state, "tenant_id", None)
    if value is None:
        raise HTTPException(status_code=401, detail="未认证")
    return int(value)


@router.get("/overview", dependencies=[Depends(require_role("admin"))])
def tenant_overview(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
):
    """当前租户运营总览；所有事件和财务数据均限定 JWT 租户。"""
    return get_overview(db, tenant_id=_tenant_id(request), days=days)


@router.get("/knowledge-quality", dependencies=[Depends(require_role("admin"))])
def knowledge_quality(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
):
    """当前租户知识健康、检索置信与低置信问题摘要。"""
    return get_knowledge_quality(db, tenant_id=_tenant_id(request), days=days)


@router.get("/platform/overview", dependencies=[Depends(require_role("admin"))])
def platform_overview(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
):
    """全平台总览：登录管理员身份之外，还必须提供独立平台运营令牌。"""
    supplied = request.headers.get("X-Platform-Token", "")
    if not settings.platform_ops_token or not secrets.compare_digest(
        supplied, settings.platform_ops_token
    ):
        raise HTTPException(status_code=403, detail="仅平台运营可查看全局指标")
    return get_overview(db, tenant_id=None, days=days)
