"""014 Widget 公共通道服务：API Key 校验、域名白名单、匿名会话令牌。

业务规则来源: PRD 3.1.3 / 3.4 / specs/014-widget-public-channel/spec.md
"""

import hashlib
import secrets

from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import create_access_token
from app.crud import tenant as tenant_crud
from app.services import session_service


class WidgetError(Exception):
    """Widget 通道业务异常基类。"""


class InvalidApiKey(WidgetError):
    """API Key 无效 -> 401。"""


class DomainNotAllowed(WidgetError):
    """来源域名不在白名单 -> 403。"""


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def find_tenant_by_api_key(db: Session, api_key: str):
    """按 API Key 哈希查找租户（仅存哈希，不存明文）。"""
    if not api_key:
        raise InvalidApiKey("API Key 无效")
    return tenant_crud.get_by_widget_key_hash(db, _hash_api_key(api_key))


def _host_from_origin(origin: str | None) -> str:
    """从 Origin/Referer 提取 hostname；非法值返回空串。"""
    if not origin:
        return ""
    origin = origin.strip()
    if "://" not in origin:
        return ""
    _, _, rest = origin.partition("://")
    host = rest.split("/", 1)[0].split(":", 1)[0]
    return host.lower()


def is_domain_allowed(tenant, origin: str | None) -> bool:
    """域名白名单校验：精确匹配或子域名匹配（entry 为父域时允许 *.entry）。"""
    host = _host_from_origin(origin)
    if not host:
        return False
    allowed_raw = (tenant.widget_allowed_domains or "").strip()
    if not allowed_raw:
        # 未配置白名单：仅开发环境允许 localhost 来源
        if settings.app_env == "development":
            return host == "localhost" or host == "127.0.0.1" or host.endswith(".localhost")
        return False
    for entry in allowed_raw.split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            return True
    return False


def create_widget_session(db: Session, *, api_key: str, origin: str | None) -> dict:
    """校验 API Key + 域名 -> 创建匿名会话 -> 签发 Widget 令牌。"""
    tenant = find_tenant_by_api_key(db, api_key)
    if tenant is None:
        raise InvalidApiKey("API Key 无效")
    if not is_domain_allowed(tenant, origin):
        raise DomainNotAllowed("来源域名未在白名单中")

    end_user_id = f"eu-{secrets.token_hex(8)}"
    created = session_service.create_session(db, tenant_id=tenant.id, end_user_id=end_user_id)
    session_id = created["session_id"]
    token = create_access_token(
        subject=f"w{session_id}",
        extra_claims={
            "tenant_id": tenant.id,
            "role": "widget",
            "session_id": session_id,
        },
    )
    return {
        "session_id": session_id,
        "token": token,
        "expires_in": settings.widget_token_expire_minutes * 60,
        "welcome_text": tenant.widget_welcome_text,
    }
