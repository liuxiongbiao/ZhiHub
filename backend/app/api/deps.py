"""
backend/app/api/deps.py

职责: 共享依赖注入 —— 数据库 session、当前用户加载、角色权限校验、租户状态校验

核心设计:
  - 中间件判断"是谁"（已注入 request.state），deps 判断"能不能做"（宪法原则 I）
  - require_role / require_tenant 是依赖工厂: 调用时返回闭包供 FastAPI Depends 使用
  - 工厂模式比装饰器灵活，允许不同路由声明不同角色白名单

边界:
  - get_current_user: request.state.user_id → 查库加载 User；不存在 → 401
  - require_role(*roles): request.state.role 不在白名单 → 403
  - require_tenant(): request.state.tenant_id → 加载 Tenant；closed → 403

依赖关系:
  - 上游: app.api.v1.* 路由通过 Depends() 注入
  - 下游: app.core.database.get_db、app.crud.user / tenant
"""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.tenant import Tenant
from app.models.user import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """从 request.state.user_id（auth_middleware 注入）查库加载 User；不存在 → 401。"""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
    user = user_crud.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
    return user


def require_role(*roles: str) -> Callable:
    """依赖工厂: 返回闭包，校验 request.state.role 在白名单内，否则 403。"""

    def _checker(request: Request) -> None:
        role = getattr(request.state, "role", None)
        if role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"无权访问：需要 {'/'.join(roles)} 角色",
            )

    return _checker


def require_tenant() -> Callable:
    """依赖工厂: 返回闭包，加载并校验租户状态。closed → 403；返回 Tenant 供路由使用。"""

    def _resolver(request: Request, db: Session = Depends(get_db)) -> Tenant:
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
        tenant = tenant_crud.get_by_id(db, tenant_id)
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
        if tenant.status == "closed":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="租户已注销")
        return tenant

    return _resolver
