"""规格 014 Widget 公共通道服务：API Key 校验、域名白名单、匿名会话令牌。

业务规则来源: PRD §3.1.3 / §3.4 / docs/specs/014-widget-public-channel/spec.md
: PUBLIC_BASE_URL 严格校验 + 会话响应品牌字段安全清洗。
"""

import hashlib
import re
import secrets
from urllib.parse import urlsplit

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


class TenantUnavailable(WidgetError):
    """租户状态不可用（readonly/closed/pending_review）-> 403"""


class PublicBaseUrlMissing(WidgetError):
    """: 生产环境未配置 PUBLIC_BASE_URL -> 嵌入代码接口 409。"""


class InvalidPublicBaseUrl(PublicBaseUrlMissing):
    """: PUBLIC_BASE_URL 配置不合法（非绝对 http/https 等）-> 409。"""


# 开发环境嵌入代码回退地址（前端 dev server 同源静态服务 /widget.js）
_DEV_PUBLIC_BASE_URL = "http://localhost:5173"

_PUBLIC_BASE_URL_ERROR = (
    "PUBLIC_BASE_URL 必须是绝对 http/https URL（不带凭据、query 或 fragment），"
    "例如 https://chat.example.com"
)

# 品牌字段安全校验（与 tenant_service 写入校验一致，读取侧兜底清洗）
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_THEME_COLOR = "#1890ff"

# 控制字符一律拒绝（含 \n \r \t \x00-\x1f \x7f）
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def is_valid_absolute_logo_url(value: str | None) -> bool:
    """: Logo URL 共享严格校验（写入校验与读取清洗共用，避免口径漂移）。

    仅允许绝对 http/https URL，且必须满足:
      - 有效 hostname（存在且可解析）
      - 无用户名/密码（凭据）、无 query、无 fragment、无控制字符
      - 端口可解析且在合法范围（解析异常即拒绝）
    用 urllib.parse 而非前缀正则，覆盖畸形 IPv6 字面量、坏端口等边界。
    """
    if not value or _CONTROL_CHARS_RE.search(value):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.username or parsed.password:
            return False
        if parsed.query or parsed.fragment:
            return False
        hostname = parsed.hostname  # 畸形 host 可能抛 ValueError
        if not hostname:
            return False
        # 端口解析异常（越界/非数字）在这里抛 ValueError → 拒绝
        _ = parsed.port
        return True
    except ValueError:
        return False


def sanitize_logo_url(value: str | None) -> str | None:
    """logo_url 读取侧兜底清洗：与写入校验同一套规则（is_valid_absolute_logo_url）。"""
    return value if is_valid_absolute_logo_url(value) else None


def sanitize_theme_color(value: str | None) -> str:
    """主题色只接受标准十六进制颜色，非法值回退默认。"""
    return value if (value and _HEX_COLOR_RE.match(value)) else _DEFAULT_THEME_COLOR


def resolve_public_base_url() -> str:
    """//: 解析并校验对外公共基地址。

    优先级: PUBLIC_BASE_URL 显式配置 > 开发环境默认 localhost:5173。
    生产环境缺失时抛 PublicBaseUrlMissing（不退回 localhost，避免假闭环）。
    显式配置必须是绝对 http/https URL：拒绝 javascript/ftp、带用户名密码、
    query、fragment 及畸形值；尾部斜杠统一规范化去除。
    : 解析过程（坏 IPv6 字面量、越界端口、控制字符）抛出的 ValueError
    一律转 InvalidPublicBaseUrl（HTTP 409），不允许 500。
    """
    configured = (settings.public_base_url or "").strip()
    if configured:
        if _CONTROL_CHARS_RE.search(configured):
            raise InvalidPublicBaseUrl(_PUBLIC_BASE_URL_ERROR)
        try:
            parsed = urlsplit(configured)
            hostname = parsed.hostname  # 畸形 host 抛 ValueError
            port = parsed.port          # 越界/非数字端口抛 ValueError
            if (
                parsed.scheme not in ("http", "https")
                or not hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise InvalidPublicBaseUrl(_PUBLIC_BASE_URL_ERROR)
        except InvalidPublicBaseUrl:
            raise
        except ValueError:
            raise InvalidPublicBaseUrl(_PUBLIC_BASE_URL_ERROR)
        return configured.rstrip("/")
    if settings.app_env in ("development", "test"):
        return _DEV_PUBLIC_BASE_URL
    raise PublicBaseUrlMissing(
        "生产环境必须配置 PUBLIC_BASE_URL（对外可访问的 https 域名）才能生成嵌入代码",
    )


def ws_endpoint_for_base(base_url: str) -> str:
    """按 http(s) 基地址推导终端用户 WebSocket 基地址: http->ws / https->wss。"""
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://"):] + "/api/v1/ws"
    return "ws://" + base_url[len("http://"):] + "/api/v1/ws"


def html_attr_escape(value: str) -> str:
    """: 嵌入代码属性值转义（& < > "），防属性注入。"""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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

    # readonly/closed/pending_review 租户拒绝新会话
    if tenant.status in ("readonly", "closed", "pending_review", "pending_deletion"):
        raise TenantUnavailable("租户当前不可用，无法发起新会话")

    end_user_id = f"eu-{secrets.token_hex(8)}"
    created = session_service.create_session(
        db, tenant_id=tenant.id, end_user_id=end_user_id, channel="widget"
    )
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
        # 品牌字段随会话响应下发（读取侧兜底清洗历史脏数据）
        "theme_color": sanitize_theme_color(tenant.widget_theme_color),
        "logo_url": sanitize_logo_url(tenant.widget_logo_url),
    }
