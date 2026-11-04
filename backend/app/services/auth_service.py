"""
backend/app/services/auth_service.py

职责: 注册事务编排（用户+租户+管理员关联）、登录校验（密码强度、失败锁定）、登出黑名单写入。

核心设计:
  认证三件套：注册（单事务建三表）、登录（失败计数+锁定+JWT 签发）、登出（jti 黑名单）。
  失败计数与锁定以 Redis 为唯一数据源（login_fail:{phone} + TTL），DB 两列不用，原因是 Redis
  计数带自动过期、高并发原子 INCR，DB 写计数有竞态且需定时清理。登录不区分"用户不存在"与
  "密码错误"，防手机号枚举攻击。登出把 JWT 的 jti 写黑名单，TTL=剩余有效期，key 过期自动清理。

关键技术点:
  - 注册单事务: user+tenant+member 三表一个事务，任一失败整体回滚（无孤儿数据）
  - 失败锁定状态机: INCR 计数 -> 达 MAX_FAIL_ATTEMPTS 锁定 -> 锁定期内即使密码正确也拒
  - 防枚举: 用户不存在与密码错误返回同一消息，不暴露手机号是否注册
  - JWT 黑名单: jti 写 Redis，TTL=token 剩余有效期，自然过期无需定时清理
  - agent presence: 登录标记在线、登出移除（规格 007 坐席认领依赖）

依赖关系:
  - 上游: app/api/v1/auth.py（HTTP 入口调 register/login/logout）
  - 下游: app/core/security.py（hash/verify/create_token）、app/core/redis.py（锁定+黑名单）、
          app/crud/user.py、app/crud/tenant.py、app/utils/validators.py、session_service（presence）

安全要点（research R1/R4）:
  - 失败计数与锁定以 Redis 为唯一数据源（login_fail:{phone} + TTL），DB 两列不用
  - 登录前先查锁定，锁定期内即使密码正确也拒绝
  - 登出将 JWT 的 jti 写入黑名单，TTL=剩余有效期
  - 注册在单事务内完成三表写入，任一失败整体回滚（无孤儿数据）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
注册流程 register()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. validate_phone(phone)   -> 格式不对抛 WeakPassword
  2. validate_password_strength(password) -> 强度不够抛 WeakPassword
  3. user_crud.get_by_phone() -> 已存在抛 PhoneAlreadyExists
  4. [事务] user_crud.create() -> create_tenant() -> add_member(admin) -> commit
     - 任一步失败 -> rollback（不留孤儿 user / tenant）
  5. 返回 RegisterResult（不含 token，前端需再调 login）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
登录流程 login()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. get_lock_ttl(phone) -> >0 -> 抛 AccountLocked(剩余秒数)
  2. user_crud.get_by_phone() + verify_password()
     - 用户不存在 或 密码错误 -> INCR 失败计数 -> 达阈值则锁定 -> 抛异常
     - 不区分"用户不存在"与"密码错误"（防手机号枚举攻击）
  3. 成功: clear_login_fail() + 查 TenantMember 取 role -> 签 JWT -> 返回 LoginResult

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
登出流程 logout()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. 计算 ttl = exp - now（令牌剩余有效期）
  2. blocklist_token(jti, ttl) -> Redis SET token:blocklist:{jti} EX ttl
     - key 自然过期即自动清理，无需定时任务
"""

import logging
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.core import redis as redis_module
from app.core.security import create_access_token, verify_password
from app.crud import tenant as tenant_crud
from app.crud import refresh_token as refresh_token_crud
from app.crud import user as user_crud
from app.services.audit_service import log_audit
from app.utils.validators import validate_email, validate_password_strength, validate_phone

logger = logging.getLogger(__name__)


def _redis_fail_open(func, *args, default=None):
    """失败计数和清零在 Redis 故障时放行，避免计数故障把正确登录打成 500。

    锁定状态查询不走这里。锁定查不到必须拒绝，见 _require_lock_ttl。
    """
    try:
        return func(*args)
    except Exception as e:
        logger.warning(f"Redis unavailable, {getattr(func, '__name__', 'redis_op')} fail-open: {e}")
        return default


def _require_lock_ttl(func, *args) -> int:
    """读取登录锁定剩余时间。Redis 故障时无法确认是否已锁定，拒绝登录。"""
    try:
        return int(func(*args))
    except Exception as e:
        logger.error(f"Redis unavailable, login lock check rejected: {e}")
        raise AuthStoreUnavailable("登录服务暂不可用") from e


# ────────── 业务异常（API 层映射到 HTTP 状态码）──────────
class AuthError(Exception):
    """认证相关业务异常基类。"""


class PhoneAlreadyExists(AuthError):
    """手机号已注册 -> 409。"""


class WeakPassword(AuthError):
    """密码强度不足 / 手机号格式错误 -> 422。"""


class InvalidCredentials(AuthError):
    """手机号或密码错误 -> 401。"""


class AuthStoreUnavailable(AuthError):
    """登录锁定状态无法确认（Redis 不可用）-> 503。"""


class AccountLocked(AuthError):
    """账号锁定中 -> 423。携带剩余锁定秒数。"""

    def __init__(self, remaining_seconds: int):
        self.remaining_seconds = remaining_seconds
        super().__init__(f"账号已锁定，请在 {remaining_seconds} 秒后重试")


# ────────── 账户体系扩展异常（PRD §3.1 #10-#17）──────────
class RateLimitExceeded(AuthError):
    """验证码发送频率超限 -> 429。"""


class InvalidCode(AuthError):
    """验证码无效/过期/错误次数超限 -> 401。"""


class EmailAlreadyExists(AuthError):
    """邮箱已注册 -> 409。"""


# ────────── 结果 DTO ──────────
@dataclass
class RegisterResult:
    user_id: int
    tenant_id: int
    phone: str
    role: str
    plan_type: str


class EmailNotVerified(AuthError):
    """邮箱账号未激活 -> 403"""


class InvalidActivationToken(AuthError):
    """激活链接无效或已过期 -> 400"""


class SmsDeliveryError(AuthError):
    """短信网关发送失败 -> 502（验证码未写入，用户可重试）。"""


class InvalidRefreshToken(AuthError):
    """Refresh token 无效、已撤销或已过期 -> 401。"""


class RefreshTokenReuseDetected(InvalidRefreshToken):
    """已消费 refresh token 被再次使用，家族已全部撤销 -> 401。"""


@dataclass
class LoginResult:
    access_token: str
    refresh_token: str
    user_id: int
    tenant_id: int
    role: str
    phone: str | None = None


@dataclass
class RefreshResult:
    access_token: str
    refresh_token: str
    family_id: str
    user_id: int
    tenant_id: int
    role: str


DEMO_PERSONA_PHONES = {
    "admin": "13900000000",
    "agent1": "13900000001",
    "agent2": "13900000002",
}
DEMO_PERSONA_ROLES = {"admin": "admin", "agent1": "agent", "agent2": "agent"}


def _refresh_digest(raw_token: str) -> str:
    """对 refresh token 做单向 SHA-256，数据库永不保存可用凭证原文。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_token_pair(
    db: Session,
    *,
    user_id: int,
    tenant_id: int,
    role: str,
    family_id: str | None = None,
    parent_token_id: int | None = None,
) -> tuple[str, str, str]:
    """签发 access/refresh 对；仅 flush，事务边界由调用 Service 掌握。"""
    raw_refresh = secrets.token_urlsafe(48)
    token_family = family_id or secrets.token_hex(16)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    refresh_token_crud.create(
        db,
        token_hash=_refresh_digest(raw_refresh),
        family_id=token_family,
        user_id=user_id,
        tenant_id=tenant_id,
        expires_at=expires_at,
        parent_token_id=parent_token_id,
    )
    access = create_access_token(
        subject=str(user_id), extra_claims={"tenant_id": tenant_id, "role": role}
    )
    return access, raw_refresh, token_family


def _demo_login_identity(db: Session, persona: str):
    """按固定手机号和角色找到当前有效演示租户。找不到时拒绝。"""
    from app.models.tenant import Tenant
    from app.models.tenant_member import TenantMember

    phone = DEMO_PERSONA_PHONES.get(persona)
    expected_role = DEMO_PERSONA_ROLES.get(persona)
    if not phone or not expected_role:
        raise InvalidCredentials("演示身份不存在")
    user = user_crud.get_by_phone(db, phone)
    if user is None:
        raise InvalidCredentials("演示身份不存在")
    row = (
        db.query(TenantMember, Tenant)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .filter(
            TenantMember.user_id == user.id,
            TenantMember.role == expected_role,
            Tenant.is_demo.is_(True),
            Tenant.status == "active",
        )
        .first()
    )
    if row is None:
        raise InvalidCredentials("演示身份不存在")
    return user, row[0], row[1]


def demo_quick_login(db: Session, *, persona: str) -> LoginResult:
    """为当前 active 演示租户签发角色令牌。

    安全边界：功能需显式开启、仅 development/test/demo 可用，并同时校验租户
    ``is_demo``、active 状态、固定手机号和精确成员角色。任一条件不满足都拒绝，
    不会退化为普通账号免密登录。
    """
    if not settings.demo_quick_login_enabled or settings.app_env not in {
        "development", "test", "demo",
    }:
        raise InvalidCredentials("演示快速登录不可用")
    user, member, tenant = _demo_login_identity(db, persona)
    access, refresh_token, _ = issue_token_pair(
        db,
        user_id=user.id,
        tenant_id=tenant.id,
        role=member.role,
    )
    if member.role == "agent":
        from app.services.session_service import mark_agent_online

        mark_agent_online(tenant.id, user.id)
    log_audit(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        action="demo_quick_login",
        target_type="user",
        target_id=user.id,
        detail=f"persona={persona}",
    )
    db.commit()
    return LoginResult(
        access_token=access,
        refresh_token=refresh_token,
        user_id=user.id,
        tenant_id=tenant.id,
        role=member.role,
        phone=user.phone,
    )


def _reject_inactive_refresh(db: Session, stored, now) -> None:
    """非活跃或过期令牌按原规则拒绝。已使用的令牌会撤销整个家族。"""
    if stored.status != "active":
        if stored.status == "used":
            refresh_token_crud.revoke_family(db, stored.family_id, now)
            db.commit()
            raise RefreshTokenReuseDetected("refresh token 已被使用，已撤销该登录家族")
        raise InvalidRefreshToken("refresh token 无效或已过期")
    if stored.expires_at <= now:
        stored.status = "expired"
        db.commit()
        raise InvalidRefreshToken("refresh token 无效或已过期")


def _claim_active_refresh(db: Session, stored, refresh_token: str, now) -> None:
    """抢占仍有效的令牌。并发下后到的请求按当前状态拒绝。"""
    if refresh_token_crud.mark_used_if_active(db, stored.id, now):
        return
    db.expire_all()
    current = refresh_token_crud.get_by_hash(db, _refresh_digest(refresh_token))
    if current is not None and current.status == "used":
        refresh_token_crud.revoke_family(db, current.family_id, now)
        db.commit()
        raise RefreshTokenReuseDetected("refresh token 已被使用，已撤销该登录家族")
    db.rollback()
    raise InvalidRefreshToken("refresh token 无效或已过期")


def refresh(db: Session, *, refresh_token: str) -> RefreshResult:
    """原子消费 refresh token 并轮换；旧值复用时撤销整个家族。"""
    stored = refresh_token_crud.get_by_hash(db, _refresh_digest(refresh_token))
    if stored is None:
        raise InvalidRefreshToken("refresh token 无效或已过期")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _reject_inactive_refresh(db, stored, now)
    _claim_active_refresh(db, stored, refresh_token, now)

    member = tenant_crud.get_member(db, tenant_id=stored.tenant_id, user_id=stored.user_id)
    if member is None:
        refresh_token_crud.revoke_family(db, stored.family_id, now)
        db.commit()
        raise InvalidRefreshToken("用户已不在该租户中")
    access, raw_refresh, family_id = issue_token_pair(
        db,
        user_id=stored.user_id,
        tenant_id=stored.tenant_id,
        role=member.role,
        family_id=stored.family_id,
        parent_token_id=stored.id,
    )
    db.commit()
    return RefreshResult(
        access_token=access,
        refresh_token=raw_refresh,
        family_id=family_id,
        user_id=stored.user_id,
        tenant_id=stored.tenant_id,
        role=member.role,
    )


def revoke_refresh_token(db: Session, *, refresh_token: str) -> None:
    """登出撤销 refresh token 所在家族；不存在时保持幂等。"""
    stored = refresh_token_crud.get_by_hash(db, _refresh_digest(refresh_token))
    if stored is not None:
        refresh_token_crud.revoke_family(
            db, stored.family_id, datetime.now(timezone.utc).replace(tzinfo=None)
        )
        db.commit()


def revoke_user_refresh_tokens(
    db: Session, *, user_id: int, tenant_id: int | None = None
) -> int:
    """撤销用户全部活跃 refresh token，供改密和管理员踢出复用。"""
    count = refresh_token_crud.revoke_user(
        db, user_id, datetime.now(timezone.utc).replace(tzinfo=None), tenant_id
    )
    db.commit()
    return count


@dataclass
class SendCodeResult:
    """验证码发送结果（demo 返回 code 供测试，生产环境不返回）。"""
    code: str
    expires_in: int
    message: str


@dataclass
class EmailRegisterResult:
    """邮箱注册结果。"""
    user_id: int
    tenant_id: int
    email: str
    role: str
    plan_type: str
    email_verified: bool = False
    activation_link: str | None = None


# ────────── 注册 ──────────
def _plan_status_and_seats(plan_type: str) -> tuple[str, int]:
    """企业版待人工审批并预留 10 个坐席，其余套餐直接开通、默认 1 个坐席。"""
    if plan_type == "enterprise":
        return "pending_review", 10
    return "active", 1


def _open_admin_tenant(
    db: Session,
    *,
    password: str,
    tenant_name: str,
    plan_type: str,
    phone: str | None = None,
    email: str | None = None,
):
    """单事务创建管理员、租户和成员。任一步失败则整单回滚。"""
    from app.core.security import hash_password

    tenant_status, seat_limit = _plan_status_and_seats(plan_type)
    try:
        user = user_crud.create(
            db,
            phone=phone,
            email=email,
            password_hash=hash_password(password),
            role_default="admin",
        )
        tenant = tenant_crud.create_tenant(
            db,
            name=tenant_name,
            plan_type=plan_type,
            seat_limit=seat_limit,
            status=tenant_status,
        )
        tenant_crud.add_member(db, tenant_id=tenant.id, user_id=user.id, role="admin")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return user, tenant


def _session_credentials(db: Session, user) -> tuple[str, str, int, str]:
    """按主租户签发令牌。坐席登录时标记在线，提交由调用方决定。"""
    member = _resolve_primary_membership(db, user.id)
    tenant_id = member.tenant_id if member else 0
    role = member.role if member else user.role_default
    token, refresh_token, _ = issue_token_pair(
        db, user_id=user.id, tenant_id=tenant_id, role=role
    )
    if role == "agent":
        from app.services.session_service import mark_agent_online
        mark_agent_online(tenant_id, user.id)
    return token, refresh_token, tenant_id, role


def _finish_password_login(db: Session, user) -> LoginResult:
    """记录登录审计并提交，返回统一的登录结果。"""
    token, refresh_token, tenant_id, role = _session_credentials(db, user)
    log_audit(
        db, tenant_id=tenant_id, user_id=user.id, action="login",
        target_type="user", target_id=user.id,
    )
    db.commit()
    return LoginResult(
        access_token=token, refresh_token=refresh_token,
        user_id=user.id, tenant_id=tenant_id, role=role, phone=user.phone,
    )


def register(db: Session, *, phone: str, password: str, tenant_name: str,
             plan_type: str = "trial", code: str | None = None) -> RegisterResult:
    """注册：单事务建 user + tenant + admin member。

    流程:
      1. 入参校验（手机号格式 + 密码强度）
      2. 手机号查重
      3. 单事务建三表，任一失败 rollback
      4. 返回 RegisterResult（不含 token，需再调 login）

    设计决策:
      单事务保证三表原子性--user/tenant/member 任一失败整体回滚，不留孤儿数据
      （如 user 建了但 tenant 没建，用户无法登录）。注册不直接返回 token，强制走 login 流程，
      保证登录失败锁定逻辑统一生效。
      plan_type="enterprise" 时 tenant.status=pending_review（待人工审批 #17）。

    Args:
        phone: 手机号
        password: 明文密码（内部 hash 后存）
        tenant_name: 租户名
        plan_type: 套餐类型（trial/basic/pro/enterprise），默认 trial
    Returns:
        RegisterResult（含 user_id/tenant_id/role/plan_type）
    """
    # 1. 入参校验（格式/强度）—— 不合规抛 WeakPassword
    # PRD §3.1.1 (US1): SMS code verification —— code=None skips (trusted internal calls)
    if code is not None:
        verify_code(phone, code)

    try:
        validate_phone(phone)
        validate_password_strength(password)
    except ValueError as e:
        raise WeakPassword(str(e)) from e

    # 2. 手机号查重
    if user_crud.get_by_phone(db, phone) is not None:
        raise PhoneAlreadyExists("该手机号已注册，请登录或找回密码")

    user, tenant = _open_admin_tenant(
        db, password=password, tenant_name=tenant_name, plan_type=plan_type, phone=phone,
    )
    return RegisterResult(
        user_id=user.id,
        tenant_id=tenant.id,
        phone=user.phone,
        role="admin",
        plan_type=tenant.plan_type,
    )


def _reject_if_login_locked(phone: str, ip: str | None) -> None:
    """锁定期内直接拒绝。Redis 无法确认锁定状态时同样拒绝。"""
    lock_ttl = _require_lock_ttl(redis_module.get_lock_ttl, phone)
    if lock_ttl > 0:
        raise AccountLocked(lock_ttl)
    if ip:
        ip_ttl = _require_lock_ttl(redis_module.get_lock_ttl_ip, ip)
        if ip_ttl > 0:
            raise AccountLocked(ip_ttl)


def _reject_failed_login(phone: str, ip: str | None) -> None:
    """累计手机号和 IP 失败次数。达到阈值后改为锁定，否则返回统一的凭据错误。"""
    count = _redis_fail_open(redis_module.incr_login_fail, phone, default=0)
    if ip:
        _redis_fail_open(redis_module.incr_login_fail_ip, ip)
    if count >= redis_module.MAX_FAIL_ATTEMPTS:
        raise AccountLocked(_require_lock_ttl(redis_module.get_lock_ttl, phone))
    raise InvalidCredentials("手机号或密码错误")


# ────────── 登录 ──────────
def login(db: Session, *, phone: str, password: str, ip: str | None = None) -> LoginResult:
    """登录：失败计数 + 锁定 + JWT 签发。

    流程:
      1. 查锁定 TTL（phone + IP 双维度），锁定期内直接拒绝
      2. 查用户 + 校验密码，失败 INCR 计数（phone + IP 双维度），达阈值锁定
      3. 成功清零计数，取 role 签 JWT，agent 标记在线

    设计决策:
      - 不区分"用户不存在"与"密码错误"，防手机号枚举--攻击者无法通过不同响应判断手机号是否注册。
      - 锁定期内即使密码正确也拒绝，防暴力破解。agent 登录标记在线（presence 集合），供坐席认领。
      - IP 维度: 失败同时累计 phone 与 IP 计数——攻击者对一个已知手机号刷错密码
        5 次先锁住自己的 IP（而非仅锁目标账号），防账号 DoS；ip 为 None（内部调用/测试）时跳过。

    Args:
        phone: 手机号
        password: 明文密码
        ip: 客户端 IP（可选；不传则不做 IP 维度）
    Returns:
        LoginResult（含 access_token/user_id/tenant_id/role）
    Raises:
        AccountLocked: 账号锁定中
        InvalidCredentials: 手机号或密码错误
    """
    _reject_if_login_locked(phone, ip)

    user = user_crud.get_by_phone(db, phone)
    if user is None or not verify_password(password, user.password_hash):
        _reject_failed_login(phone, ip)

    # 3. 成功：清零 phone 维度失败计数，取该用户在某租户的 admin/agent 角色签发令牌
    # 不清 IP 维度计数——任意用户成功登录就清 IP 计数会让攻击者用自己合法账号
    # 重置 IP 计数绕过 IP 锁（credential stuffing）；IP 计数只靠 TTL 自然过期。
    _redis_fail_open(redis_module.clear_login_fail, phone)
    return _finish_password_login(db, user)


def _resolve_primary_membership(db: Session, user_id: int):
    """取用户的主租户成员关联（本 feature 一个用户注册即一个租户，取第一条）。"""
    from sqlalchemy import select

    from app.models.tenant_member import TenantMember

    return db.execute(
        select(TenantMember).where(TenantMember.user_id == user_id).order_by(TenantMember.id)
    ).scalars().first()


# ────────── 登出 ──────────
def logout(db: Session, *, jti: str, exp: int, user_id: int | None = None,
           tenant_id: int | None = None, role: str | None = None) -> None:
    """登出：jti 写黑名单（TTL=剩余有效期）+ agent 移除在线标记 + 审计日志。

    流程:
      1. 计算 ttl = exp - now
      2. blocklist_token 写黑名单
      3. agent 则 mark_agent_offline
      4. PRD 2.1 审计日志: 登出落库（logout 无其他 DB 写入，需显式 commit）

    设计决策:
      TTL=token 剩余有效期，key 自然过期自动清理，无需定时任务。agent 登出移除 presence，
      避免已下线坐席仍被分配会话。

    规格 007: 若 role=agent，同时从在线坐席集合移除。
    """
    ttl = exp - int(time.time())
    redis_module.blocklist_token(jti, ttl)
    if role == "agent" and tenant_id is not None and user_id is not None:
        from app.services.session_service import mark_agent_offline
        mark_agent_offline(tenant_id, user_id)
    # PRD §2.1 审计日志: 登出落库（tenant_id 为空时跳过，无法隔离的记录不写）
    if tenant_id is not None:
        log_audit(
            db, tenant_id=tenant_id, user_id=user_id, action="logout",
            target_type="user", target_id=user_id,
        )
        db.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 账户体系扩展（PRD §3.1 #10-#17，不含 #12 微信）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 验证码 Redis 键前缀与常量
VCODE_TTL_SECONDS = 300          # 验证码有效期 5 分钟
VCODE_FREQ_SECONDS = 60          # 发送频率限制: 1 分钟/次
VCODE_DAILY_LIMIT = 10           # 每日发送上限: 24h/10 次
VCODE_DAILY_TTL_SECONDS = 86400  # 每日计数窗口 24 小时
VCODE_MAX_FAIL_ATTEMPTS = 3      # 防爆破: 3 次错误验证码失效


def _vcode_key(target: str, code_type: str = "sms") -> str:
    """验证码 Redis key: vcode:{type}:{target}。"""
    return f"vcode:{code_type}:{target}"


def _vcode_fail_key(target: str, code_type: str = "sms") -> str:
    """验证码失败计数 key: vcode_fail:{type}:{target}。"""
    return f"vcode_fail:{code_type}:{target}"


def _vcode_freq_key(target: str, code_type: str = "sms") -> str:
    """验证码发送频率限制 key: vcode_freq:{type}:{target}（TTL=60s）。"""
    return f"vcode_freq:{code_type}:{target}"


def _vcode_daily_key(target: str, code_type: str = "sms") -> str:
    """验证码每日计数 key: vcode_daily:{type}:{target}（TTL=24h）。"""
    return f"vcode_daily:{code_type}:{target}"


# ────────── 验证码发送（#10 send_code / #14 频率限制）──────────
def _enforce_verification_limits(redis_client, target: str, code_type: str) -> None:
    """1 分钟内不可重发，24 小时内不超过每日上限。"""
    if redis_client.exists(_vcode_freq_key(target, code_type)):
        raise RateLimitExceeded("发送过于频繁，请 1 分钟后重试")
    daily_count = int(redis_client.get(_vcode_daily_key(target, code_type)) or 0)
    if daily_count >= VCODE_DAILY_LIMIT:
        raise RateLimitExceeded("今日验证码发送次数已达上限")


def _deliver_sms_code(target: str, code: str) -> None:
    """真实短信失败时不写验证码，也不消耗发送额度。"""
    from app.core.sms_client import get_sms_client

    try:
        client = get_sms_client()
    except Exception as exc:
        logger.warning(f"SMS client unavailable (target={target}): {exc}")
        raise SmsDeliveryError("短信服务未启用或配置不完整") from None
    if client.is_mock:
        return
    try:
        ok = client.send(target, code)
    except Exception as exc:
        logger.warning(f"SMS send failed (target={target}): {exc}")
        ok = False
    if not ok:
        raise SmsDeliveryError("短信发送失败，请稍后重试")


def _store_verification_code(redis_client, target: str, code_type: str, code: str) -> None:
    """验证码只存哈希。写入成功后才开始限频和每日计数。"""
    redis_client.set(
        _vcode_key(target, code_type),
        hashlib.sha256(code.encode("utf-8")).hexdigest(),
        ex=VCODE_TTL_SECONDS,
    )
    redis_client.set(_vcode_freq_key(target, code_type), "1", ex=VCODE_FREQ_SECONDS)
    new_count = redis_client.incr(_vcode_daily_key(target, code_type))
    if new_count == 1:
        redis_client.expire(_vcode_daily_key(target, code_type), VCODE_DAILY_TTL_SECONDS)
    redis_client.delete(_vcode_fail_key(target, code_type))


def send_verification_code(target: str, code_type: str = "sms", reveal_code: bool | None = None) -> SendCodeResult:
    """发送验证码: 生成 6 位码写 Redis + 频率限制（1min/次、24h/10次）。

    demo 模式: 不接阿里云，验证码直接写 Redis，返回结果含 code 供测试。
    生产环境: code 字段从响应中移除，改为调用短信网关发送。

    流程:
      1. 频率限制: vcode_freq key 存在 -> 1 分钟内已发过 -> RateLimitExceeded
      2. 每日上限: vcode_daily >= 10 -> 24h 内已达上限 -> RateLimitExceeded
      3. 生成 6 位随机码（secrets.randbelow 密码学安全随机）
      4. 写 Redis: vcode key TTL=5min
      5. 设频率限制: vcode_freq key TTL=60s
      6. 每日计数 INCR: 首次设 TTL=24h
      7. 清零失败计数: DEL vcode_fail key（新码覆盖旧码，失败计数归零）

    Args:
        target: 手机号（code_type=sms）或邮箱（code_type=email）
        code_type: "sms" 或 "email"
    Returns:
        SendCodeResult（含 code 供 demo 测试）
    Raises:
        RateLimitExceeded: 1 分钟内重复发送 或 24h 内超 10 次
    """
    r = redis_module.get_redis()
    _enforce_verification_limits(r, target, code_type)
    code = f"{secrets.randbelow(1000000):06d}"
    if code_type == "sms":
        _deliver_sms_code(target, code)
    _store_verification_code(r, target, code_type, code)

    from app.config import settings
    reveal = settings.app_env == "test" if reveal_code is None else reveal_code
    return SendCodeResult(
        code=code if reveal else "", expires_in=VCODE_TTL_SECONDS, message="验证码已发送"
    )


# ────────── 验证码校验（#13 防爆破）──────────
def verify_code(target: str, code: str, code_type: str = "sms") -> bool:
    """校验验证码: 匹配返回 True，不匹配抛 InvalidCode。

    防爆破: 3 次错误后验证码失效（DEL vcode key），即使之后输入正确码也失败。

    流程:
      1. 取 Redis 中的验证码，不存在 -> InvalidCode（过期/未发送）
      2. 匹配 -> DEL 验证码 + 失败计数，返回 True
      3. 不匹配 -> INCR 失败计数
         - 达 3 次 -> DEL 验证码（失效），抛 InvalidCode
         - 未达 3 次 -> 抛 InvalidCode

    Args:
        target: 手机号或邮箱
        code: 用户输入的验证码
        code_type: "sms" 或 "email"
    Returns:
        True 校验通过
    Raises:
        InvalidCode: 验证码无效/过期/错误次数超限
    """
    r = redis_module.get_redis()
    stored = r.get(_vcode_key(target, code_type))

    # 1. 验证码不存在（过期/未发送）
    if not stored:
        raise InvalidCode("验证码无效或已过期")

    # 2. 匹配（哈希比对）→ 清理并返回
    if stored == hashlib.sha256(code.encode("utf-8")).hexdigest():
        r.delete(_vcode_key(target, code_type))
        r.delete(_vcode_fail_key(target, code_type))
        return True

    # 3. 不匹配 → 失败计数 +1，达 3 次失效
    fail_count = r.incr(_vcode_fail_key(target, code_type))
    # 失败计数 key 跟验证码同生命周期
    r.expire(_vcode_fail_key(target, code_type), VCODE_TTL_SECONDS)
    if fail_count >= VCODE_MAX_FAIL_ATTEMPTS:
        r.delete(_vcode_key(target, code_type))
        r.delete(_vcode_fail_key(target, code_type))
    raise InvalidCode("验证码错误")


# ────────── 验证码登录（#10 login_by_code）──────────
def login_by_code(db: Session, *, phone: str, code: str) -> LoginResult:
    """手机验证码登录: 校验验证码 -> 查用户 -> 签 JWT。

    防枚举: 验证码错误与用户不存在返回同一异常 InvalidCode，不暴露手机号是否注册。

    流程:
      1. verify_code(phone, code) -> 失败抛 InvalidCode
      2. 查用户 -> 不存在抛 InvalidCode（与验证码错误同一异常，防枚举）
      3. 取租户角色 -> 签 JWT -> 返回 LoginResult

    Args:
        phone: 手机号
        code: 验证码
    Returns:
        LoginResult
    Raises:
        InvalidCode: 验证码无效或用户不存在
    """
    # 1. 校验验证码
    try:
        verify_code(phone, code, code_type="sms")
    except InvalidCode:
        raise

    # 2. 查用户（不存在 → InvalidCode，与验证码错误同一异常防枚举）
    user = user_crud.get_by_phone(db, phone)
    if user is None:
        raise InvalidCode("验证码无效或已过期")
    return _finish_password_login(db, user)


# ────────── 邮箱注册（#11）──────────
def _email_verify_key(token: str) -> str:
    return f"email_verify:{token}"


def _issue_email_activation(user_id: int, email: str, reveal: bool) -> str | None:
    """生成激活 token 并尝试发邮件；reveal=True 时返回可在响应中回显的链接。"""
    token = secrets.token_urlsafe(32)
    ttl = settings.email_verify_token_ttl_hours * 3600
    redis_module.get_redis().set(_email_verify_key(token), str(user_id), ex=ttl)

    from app.core.email_client import build_activation_link, send_email
    link = build_activation_link(token)
    try:
        send_email(
            to=email,
            subject="ZhiHub account activation",
            text=f"Click the link below to activate your account (valid for {settings.email_verify_token_ttl_hours} hours):\n{link}",
        )
    except Exception as e:
        logger.warning(f"Activation email send failed (user={user_id}): {e}")
    return link if reveal else None


def _resend_rate_key(email: str) -> str:
    return f"email_activate_resend:{email}"


def register_by_email(
    db: Session, *, email: str, password: str, tenant_name: str,
    plan_type: str = "trial", activation_link_revealed: bool = False,
) -> EmailRegisterResult:
    """邮箱注册: 单事务建 user(含 email) + tenant + admin member。

    流程:
      1. 入参校验（邮箱格式 + 密码强度）
      2. 邮箱查重
      3. 单事务建三表，任一失败 rollback
      4. 返回 EmailRegisterResult

    企业版（plan_type="enterprise"）: tenant.status=pending_review（#17 待人工审批）。

    Args:
        email: 邮箱（登录名）
        password: 明文密码
        tenant_name: 租户名
        plan_type: 套餐类型，默认 trial
    Returns:
        EmailRegisterResult
    Raises:
        WeakPassword: 邮箱格式不正确 或 密码强度不足
        EmailAlreadyExists: 邮箱已注册
    """
    # 1. 入参校验
    try:
        validate_email(email)
        validate_password_strength(password)
    except ValueError as e:
        raise WeakPassword(str(e)) from e

    # 2. 邮箱查重
    if user_crud.get_by_email(db, email) is not None:
        raise EmailAlreadyExists("该邮箱已注册，请登录或找回密码")

    user, tenant = _open_admin_tenant(
        db, password=password, tenant_name=tenant_name, plan_type=plan_type, email=email,
    )
    activation_link = None
    if settings.email_verify_required:
        activation_link = _issue_email_activation(user.id, email, activation_link_revealed)

    return EmailRegisterResult(
        user_id=user.id,
        tenant_id=tenant.id,
        email=user.email,
        role="admin",
        plan_type=tenant.plan_type,
        email_verified=False,
        activation_link=activation_link,
    )


# ────────── 邮箱密码登录（#11）──────────
def activate_email(db: Session, *, token: str) -> dict:
    """校验激活 token 并标记邮箱已验证。"""
    r = redis_module.get_redis()
    raw = r.get(_email_verify_key(token))
    if not raw:
        raise InvalidActivationToken("激活链接无效或已过期")
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        raise InvalidActivationToken("激活链接无效或已过期")
    user = user_crud.get_by_id(db, user_id)
    if user is None:
        raise InvalidActivationToken("激活链接无效或已过期")
    user.email_verified = True
    r.delete(_email_verify_key(token))
    db.commit()
    return {"user_id": user.id, "email": user.email, "activated": True}


def resend_email_activation(db: Session, *, email: str) -> dict:
    """重新发送激活邮件（60s 限频；用户不存在/已激活统一返回成功，防枚举）。"""
    r = redis_module.get_redis()
    if r.exists(_resend_rate_key(email)):
        raise RateLimitExceeded("发送过于频繁，请 1 分钟后重试")
    user = user_crud.get_by_email(db, email)
    if user is None or user.email_verified or not user.email:
        return {"sent": False}
    _issue_email_activation(user.id, user.email, reveal=False)
    r.set(_resend_rate_key(email), "1", ex=60)
    return {"sent": True}


def login_by_email(db: Session, *, email: str, password: str) -> LoginResult:
    """邮箱密码登录: 查用户 + 校验密码 + 签 JWT。

    防枚举: 用户不存在与密码错误返回同一异常 InvalidCredentials。

    Args:
        email: 邮箱
        password: 明文密码
    Returns:
        LoginResult
    Raises:
        InvalidCredentials: 邮箱或密码错误
    """
    user = user_crud.get_by_email(db, email)
    if user is not None and settings.email_verify_required and not user.email_verified \
            and verify_password(password, user.password_hash):
        raise EmailNotVerified("邮箱尚未激活，请先点击激活链接完成验证")
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials("邮箱或密码错误")
    return _finish_password_login(db, user)


# ────────── 密码找回（#16）──────────
def reset_password(
    db: Session, *, target: str, code: str, new_password: str,
    code_type: str = "sms",
) -> None:
    """密码找回: 验证码验证身份 -> 重置密码。

    流程:
      1. verify_code(target, code) -> 失败抛 InvalidCode
      2. 校验新密码强度
      3. 按 code_type 查用户（sms->phone, email->email）
      4. 用户不存在 -> InvalidCode（防枚举，不暴露）
      5. 更新 password_hash -> commit

    Args:
        target: 手机号（code_type=sms）或邮箱（code_type=email）
        code: 验证码
        new_password: 新密码
        code_type: "sms" 或 "email"
    Raises:
        InvalidCode: 验证码无效或用户不存在
        WeakPassword: 新密码强度不足
    """
    # 1. 验证码校验身份
    try:
        verify_code(target, code, code_type=code_type)
    except InvalidCode:
        raise

    # 2. 新密码强度校验
    try:
        validate_password_strength(new_password)
    except ValueError as e:
        raise WeakPassword(str(e)) from e

    # 3. 查用户
    if code_type == "sms":
        user = user_crud.get_by_phone(db, target)
    else:
        user = user_crud.get_by_email(db, target)

    # 4. 用户不存在 → InvalidCode（防枚举）
    if user is None:
        raise InvalidCode("验证码无效或已过期")

    # 5. 更新密码
    from app.core.security import hash_password
    user.password_hash = hash_password(new_password)
    refresh_token_crud.revoke_user(
        db, user.id, datetime.now(timezone.utc).replace(tzinfo=None)
    )
    db.commit()


# ────────── 账号绑定邮箱（#15）──────────
def bind_email(db: Session, *, user_id: int, email: str, code: str) -> None:
    """绑定邮箱: 验证码校验 + 唯一性校验 -> 写入 user.email。

    流程:
      1. verify_code(email, code, "email") -> 失败抛 InvalidCode
      2. 邮箱查重 -> 已被其他用户占用 -> EmailAlreadyExists
      3. 查用户 -> 不存在 -> InvalidCode（防枚举）
      4. user.email = email -> commit

    Args:
        user_id: 当前登录用户 ID
        email: 待绑定邮箱
        code: 邮箱验证码
    Raises:
        InvalidCode: 验证码无效
        EmailAlreadyExists: 邮箱已被其他用户绑定
    """
    # 1. 验证码校验
    try:
        verify_code(email, code, code_type="email")
    except InvalidCode:
        raise

    # 2. 邮箱查重
    existing = user_crud.get_by_email(db, email)
    if existing is not None and existing.id != user_id:
        raise EmailAlreadyExists("该邮箱已被其他账号绑定")

    # 3. 查用户并绑定
    user = user_crud.get_by_id(db, user_id)
    if user is None:
        raise InvalidCode("验证码无效或已过期")

    user.email = email
    user.email_verified = True
    db.commit()


# ────────── 账号绑定手机号（PRD §3.1 #15 反向绑定）──────────
def bind_phone(db: Session, *, user_id: int, phone: str, code: str) -> None:
    """邮箱注册用户绑定手机号：验证码校验 + 唯一性校验 -> 写入 user.phone。

    与 bind_email 对称，补齐"邮箱注册用户可绑定手机号"的账号绑定闭环。
    """
    # 1. 验证码校验
    try:
        verify_code(phone, code, code_type="sms")
    except InvalidCode:
        raise

    # 2. 格式校验
    try:
        validate_phone(phone)
    except ValueError as e:
        raise WeakPassword(str(e)) from e

    # 3. 手机号查重（唯一性）
    existing = user_crud.get_by_phone(db, phone)
    if existing is not None and existing.id != user_id:
        raise PhoneAlreadyExists("该手机号已被其他账号绑定")

    # 4. 绑定（同号重复绑定幂等）
    user = user_crud.get_by_id(db, user_id)
    if user is None:
        raise InvalidCredentials("用户不存在")
    if user.phone == phone:
        return
    user.phone = phone
    db.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 微信扫码登录（PRD §3.1 #12）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WechatStateError(AuthError):
    """微信 OAuth state 校验失败（缺失/不匹配/过期），拒绝回调（防 CSRF，）。"""


class WechatBindConflict(AuthError):
    """微信 openid 已被其他账号绑定 -> 409。"""


@dataclass
class WechatLoginResult:
    """微信扫码登录结果。

    need_bind=True 时 access_token=None，前端引导用户登录后调 wechat_bind。
    need_bind=False 时已签发 token，前端直接完成登录。
    """
    need_bind: bool
    openid: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    user_id: int | None = None
    tenant_id: int | None = None
    role: str | None = None


# 微信 OAuth state 有效期（秒）: 覆盖用户扫码+授权窗口，过期视为 CSRF 攻击
_WECHAT_STATE_TTL_SECONDS = 10 * 60


def wechat_login_start(*, redirect_uri: str) -> dict:
    """微信登录第一步：生成授权 URL 给前端跳转。

    调 WeChatClient.get_auth_url 拼装 OAuth2 授权 URL，前端 window.location 跳转。
    mock 模式返回假 URL，真实模式返回微信授权页 URL。

    设计决策:
      state 由服务端生成并存入 Redis（一次性、TTL 10min），回调时校验——
      不信任客户端传入的 state（客户端可控即 CSRF 防护失效）。

    Args:
        redirect_uri: 授权后回调地址（需在公众号后台配置可信域名）
    Returns:
        dict: {"auth_url": "..."}（state 已内嵌进 auth_url，回调原样带回）
    """
    import uuid as _uuid

    from app.core.redis import get_redis
    from app.core.wechat_client import get_wechat_client

    # 服务端生成一次性 state，存 Redis（TTL 覆盖扫码窗口）
    state = _uuid.uuid4().hex
    r = get_redis()
    r.set(f"wechat:state:{state}", "1", ex=_WECHAT_STATE_TTL_SECONDS)

    client = get_wechat_client()
    url = client.get_auth_url(redirect_uri, state)
    return {"auth_url": url}


def _verify_wechat_state(state: str | None) -> None:
    """校验回调 state（防 OAuth CSRF，）。

    流程: GET + DEL（一次性使用）-> state 不存在/已用 -> WechatStateError。
    安全取舍: Redis 异常同样拒绝（fail-closed），防攻击者绕过校验；
    代价是 Redis 故障期间微信登录不可用（微信登录本就是低频辅助登录）。
    """
    if not state:
        raise WechatStateError("缺少 state 参数")
    from app.core.redis import get_redis
    r = get_redis()
    try:
        # L2: GETDEL 原子消费（Redis 6.2+/redis-py>=4.2/fakeredis 均支持）——
        # 原 GET+DEL 两步非原子，并发回调可在两次往返间复用同一 state 两次
        consumed = r.getdel(f"wechat:state:{state}")
        if not consumed:
            raise WechatStateError("state 无效或已过期")
    except WechatStateError:
        raise
    except Exception as e:  # Redis 故障: 拒绝而非放行
        raise WechatStateError("state 校验失败（服务暂不可用）") from e


def wechat_login_callback(db: Session, *, code: str, state: str | None = None) -> WechatLoginResult:
    """微信回调：校验 state -> code 换 openid -> 查绑定 -> 签 JWT 或返回 need_bind。

    流程:
      0. 校验 state（一次性，防 OAuth CSRF）
      1. WeChatClient.exchange_code_for_openid(code) -> openid
      2. user_crud.get_by_openid(openid) -> 已绑定 -> 签 JWT 返回
      3. 未绑定 -> 返回 need_bind=True + openid（不自动创建用户）

    设计决策:
      - state 必须由 wechat_login_start 签发（Redis 一次性校验），缺失即拒绝
      - 未绑定时不自动创建用户，需引导用户先注册/登录再调 wechat_bind 绑定 code。
        这样保证每个微信用户都关联到一个已注册的租户账号，避免孤儿微信用户。

    Args:
        db: 数据库会话
        code: 微信回调携带的授权 code
        state: 微信回调原样带回的 state（wechat_login_start 签发）
    Returns:
        WechatLoginResult（need_bind=False 含 token / need_bind=True 含 openid）
    """
    from app.core.wechat_client import get_wechat_client

    _verify_wechat_state(state)
    client = get_wechat_client()
    openid = client.exchange_code_for_openid(code)

    user = user_crud.get_by_openid(db, openid)
    if user is None:
        return WechatLoginResult(need_bind=True, openid=openid)

    token, refresh_token, tenant_id, role = _session_credentials(db, user)
    db.commit()
    return WechatLoginResult(
        need_bind=False, openid=openid, access_token=token, refresh_token=refresh_token,
        user_id=user.id, tenant_id=tenant_id, role=role,
    )


def wechat_bind(db: Session, *, user_id: int, code: str) -> str:
    """已登录用户绑定微信到自己的账号（: 用 code 而非 openid）。

    流程:
      0. 服务端用 code 换 openid —— openid 由微信授权颁发，无法伪造，
         杜绝"客户端任意传 openid 绑定他人微信"的账户接管链
      1. openid 查重 -> 已被其他用户绑定 -> WechatBindConflict
      2. 同一用户重复绑同一 openid -> 幂等返回（不报错）
      3. 写入 user.wechat_openid -> commit

    设计决策:
      - 绑定必须走微信授权回调（code 换 openid），不接受客户端直接传 openid：
        否则 openid 泄露后，攻击者登录自己账号绑定受害者 openid，
        受害者之后扫码将登录攻击者账号（账户接管）。
      - 一个 openid 只能绑定一个账号（唯一约束），防一个微信号关联多账号。
      - 同一用户重复绑同一 openid 幂等（不报错），提升用户体验。

    Args:
        db: 数据库会话
        user_id: 当前登录用户 ID
        code: 微信授权 code（一次性，5 分钟过期）
    Returns:
        str: 绑定成功的 openid
    Raises:
        WechatBindConflict: openid 已被其他账号绑定
    """
    from app.core.wechat_client import get_wechat_client

    openid = get_wechat_client().exchange_code_for_openid(code)

    existing = user_crud.get_by_openid(db, openid)
    # openid 已被其他用户绑定 → 冲突
    if existing is not None and existing.id != user_id:
        raise WechatBindConflict("该微信已绑定其他账号")
    # 同一用户重复绑同一 openid → 幂等返回
    if existing is not None and existing.id == user_id:
        return openid

    user = user_crud.get_by_id(db, user_id)
    if user is None:
        raise InvalidCredentials("用户不存在")

    user.wechat_openid = openid
    db.commit()
    return openid
