"""
backend/app/services/tenant_service.py

职责: 配置向导状态推进、成员邀请

核心设计:
  租户入驻后的配置向导状态机管理（basic -> knowledge -> mcp -> team -> done），
  以及管理员邀请新成员加入租户的链接生成与接受逻辑。

关键技术点:
  - 向导状态机: 显式有序列表 WIZARD_STEPS，advance 仅允许 index+1，防止跳步
  - 邀请令牌: 生成一次性 token 写 Redis 带 TTL，接受后 DEL 立即失效；过期 key 自然消失
  - seat_limit 校验在 invite 时而非 accept 时，避免发了邀请却无法兑现
  - 接受邀请单事务建 user + member，任一失败整体回滚（无孤儿数据）

依赖关系:
  - 上游: app/api/v1/tenant.py（配置向导 / 邀请端点）
  - 下游: app.crud.tenant（持久化）、app.crud.user、app.core.redis、app.core.security
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

# Widget 品牌/域名白名单校验规则
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# 规范 hostname（含单标签）或 IPv4 或 localhost；禁止 scheme/路径/query/fragment/端口/凭据
_DOMAIN_ENTRY_RE = re.compile(
    r"^(localhost|"
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}|"
    r"(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    r"(\.([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?))*)$"
)

from sqlalchemy.orm import Session

from app.core import redis as redis_module
from app.config import settings
from app.core.security import generate_random_token, hash_password
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.tenant import Tenant
from app.services.audit_service import log_audit
from app.utils.validators import validate_password_strength, validate_phone


# 持久化状态保持原有五态；渠道作为上线条件与 team 一起在最后一步校验，
# 避免给历史租户和已有 API 契约引入一次不必要的数据迁移。
WIZARD_STEPS = ["basic", "knowledge", "mcp", "team", "done"]
WIZARD_CHECK_STEPS = ["basic", "knowledge", "mcp", "team", "channel", "done"]
WIZARD_META = {
    "basic": ("企业信息", "确认企业名称与套餐", "/settings"),
    "knowledge": ("知识准备", "至少一份文档已完成解析并可检索", "/knowledge/list"),
    "mcp": ("业务能力", "订单与工单工具链可用", "/demo-center"),
    "team": ("人工团队", "至少一名坐席可以接管会话", "/onboarding/members"),
    "channel": ("客户入口", "生成 Widget API Key 与嵌入通道", "/widget/config"),
    "done": ("投入使用", "全部关键能力已就绪", "/"),
}

# 邀请 token 有效期：7 天（PRD §3.1 邀请链接带过期）
INVITE_TTL_SECONDS = 7 * 24 * 60 * 60

# 套餐矩阵是升级、席位和计费配额的唯一业务口径（PRD §3.9）。
PLAN_CATALOG = {
    "trial": {
        "name": "试用版", "monthly_price": 0.0, "conversation_quota": 100,
        "knowledge_base_limit": 1, "storage_quota_mb": 500, "seat_limit": 1,
        "negotiable": False, "rank": 0,
    },
    "basic": {
        "name": "基础版", "monthly_price": 999.0, "conversation_quota": 2000,
        "knowledge_base_limit": 3, "storage_quota_mb": 5 * 1024, "seat_limit": 2,
        "negotiable": False, "rank": 1,
    },
    "pro": {
        "name": "专业版", "monthly_price": 4999.0, "conversation_quota": 10000,
        "knowledge_base_limit": None, "storage_quota_mb": 50 * 1024, "seat_limit": 10,
        "negotiable": False, "rank": 2,
    },
    "enterprise": {
        "name": "企业版", "monthly_price": None, "conversation_quota": 100000,
        "knowledge_base_limit": None, "storage_quota_mb": None, "seat_limit": 10,
        "negotiable": True, "rank": 3,
    },
}


# ────────── 业务异常（API 层映射到 HTTP 状态码）──────────
class TenantError(Exception):
    """租户业务异常基类。"""


class TenantNotFound(TenantError):
    """租户不存在 -> 404。"""


class WizardAlreadyDone(TenantError):
    """向导已完成，不可再推进 -> 409。"""


class WizardStepIncomplete(TenantError):
    """当前步骤尚未通过真实能力检查 -> 409。"""


class SeatLimitExceeded(TenantError):
    """租户成员数已达 seat_limit -> 409。"""


class InvalidInviteToken(TenantError):
    """邀请 token 无效/过期/手机号不匹配 -> 410。"""


class TenantNotPending(TenantError):
    """租户非待审批状态 -> 409（PRD 3.1 #17 企业版审批）。"""


class InvalidPlanChange(TenantError):
    """套餐目标非法、重复升级或尝试降级。"""


class TenantCancellationConflict(TenantError):
    """注销申请重复、不可撤销或状态冲突 -> 409。"""


# ────────── 配置向导 ──────────
class TenantInactive(TenantError):
    """租户状态不允许操作（readonly/closed/pending_review）-> 403"""


# 不允许发起新会话的租户状态（PRD §3.1：试用到期只读/欠费只读/已注销/待审批）
BLOCKED_NEW_SESSION_STATUSES = {"readonly", "closed", "pending_review", "pending_deletion"}


def require_active_tenant(db: Session, tenant_id: int):
    """租户状态门禁：仅 active 允许发起新会话。"""
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if tenant.status in BLOCKED_NEW_SESSION_STATUSES:
        raise TenantInactive(f"租户当前状态为 {tenant.status}，无法发起新会话")
    return tenant


def require_message_allowed_tenant(db: Session, tenant_id: int):
    """消息入口门禁：closed/注销等待期拒绝；readonly 允许进行中会话走完。"""
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        # 会话存在但租户行缺失（脏数据/测试夹具）：放行并告警，避免主链路 500
        import logging
        logging.getLogger(__name__).warning(
            f"require_message_allowed_tenant: tenant {tenant_id} not found, allowing"
        )
        return None
    if tenant.status in {"closed", "pending_deletion"}:
        raise TenantInactive("租户已申请注销，无法继续会话")
    return tenant


def _wizard_readiness(db: Session, tenant: Tenant) -> tuple[dict, dict]:
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.tenant_member import TenantMember

    ready_docs = db.query(KnowledgeDocument.id).filter(
        KnowledgeDocument.tenant_id == tenant.id,
        KnowledgeDocument.status == "ready",
        KnowledgeDocument.is_latest.is_(True),
        KnowledgeDocument.quality_status == "ready",
    ).count()
    agents = db.query(TenantMember.id).filter(
        TenantMember.tenant_id == tenant.id,
        TenantMember.role == "agent",
    ).count()
    ready = {
        "basic": bool(tenant.name.strip() and tenant.plan_type),
        "knowledge": ready_docs > 0,
        "mcp": settings.agent_tool_mode in {"mcp", "fc"},
        "team": agents > 0,
        "channel": bool(tenant.widget_api_key_hash),
    }
    ready["done"] = all(ready.values())
    details = {
        "basic": f"企业：{tenant.name} · 套餐：{tenant.plan_type}",
        "knowledge": f"{ready_docs} 份可检索文档" if ready_docs else "尚无可检索文档",
        "mcp": "内置订单/工单工具链已启用" if ready["mcp"] else "业务工具链未启用",
        "team": f"{agents} 名坐席已加入" if agents else "尚未邀请坐席",
        "channel": "Widget 通道已生成" if ready["channel"] else "尚未生成 Widget API Key",
        "done": "系统已具备对外服务条件" if ready["done"] else "仍有步骤需要完成",
    }
    return ready, details


def _wizard_checks(db: Session, tenant: Tenant) -> list[dict]:
    """按真实数据计算每一步就绪状态，不以“点击过下一步”代替配置完成。"""
    ready, details = _wizard_readiness(db, tenant)
    checks = []
    for step in WIZARD_CHECK_STEPS:
        label, description, action_path = WIZARD_META[step]
        checks.append({
            "step": step,
            "label": label,
            "description": description,
            "ready": ready[step],
            "detail": details[step],
            "action_path": action_path,
        })
    return checks


def get_wizard_status(db: Session, tenant_id: int) -> dict:
    """查询向导状态：当前步骤 + 完成度 + 步骤全表。

    Args:
        tenant_id: 租户 ID
    Returns:
        {"current_step", "completed", "total_steps", "steps"}
    Raises:
        TenantNotFound: 租户不存在
    """
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    checks = _wizard_checks(db, tenant)
    return {
        "current_step": tenant.wizard_step,
        "completed": tenant.wizard_step == WIZARD_STEPS[-1],
        "total_steps": len(WIZARD_STEPS),
        "steps": list(WIZARD_STEPS),
        "checks": checks,
    }


def advance_wizard(
    db: Session,
    tenant_id: int,
    *,
    enforce_checks: bool = False,
) -> dict:
    """推进向导到下一步。

    校验: 当前步骤必须在 WIZARD_STEPS 中且非最后一步；done 不可再推进。
    设计决策: 显式 index+1 推进，不接受目标 step 参数，杜绝跳步
    （如 basic 直接 POST 到 team）。调用方无法绕过顺序。

    Args:
        tenant_id: 租户 ID
    Returns:
        推进后的 wizard status dict（同 get_wizard_status）
    Raises:
        TenantNotFound: 租户不存在
        WizardAlreadyDone: 已在 done，不可推进
    """
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if tenant.wizard_step not in WIZARD_STEPS:
        # 防御：异常数据 → 视为不可推进
        raise TenantError(f"向导步骤异常: {tenant.wizard_step}")
    idx = WIZARD_STEPS.index(tenant.wizard_step)
    if idx == len(WIZARD_STEPS) - 1:
        raise WizardAlreadyDone("配置向导已完成")
    if enforce_checks:
        checks = _wizard_checks(db, tenant)
        current_checks = [
            item for item in checks
            if item["step"] == tenant.wizard_step
            or (tenant.wizard_step == "team" and item["step"] == "channel")
        ]
        incomplete = next((item for item in current_checks if not item["ready"]), None)
        if incomplete:
            raise WizardStepIncomplete(
                f"{incomplete['label']}尚未完成：{incomplete['detail']}"
            )
    tenant.wizard_step = WIZARD_STEPS[idx + 1]
    # PRD §2.1 审计日志: 向导推进落库（同事务）
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="wizard_advance",
        target_type="tenant", target_id=tenant_id,
        detail=f"step={WIZARD_STEPS[idx + 1]}",
    )
    db.commit()
    return get_wizard_status(db, tenant_id)


# ────────── 成员邀请 ──────────
def _invite_key(token: str) -> str:
    """构造邀请 token 的 Redis key。"""
    return f"invite:{token}"


def invite_member(db: Session, tenant_id: int, phone: str) -> dict:
    """admin 邀请新坐席：校验 seat_limit -> 生成 token 写 Redis。

    校验:
      - 租户存在
      - 当前成员数 < seat_limit（否则 SeatLimitExceeded）
      - 手机号格式合法
    token 内容: JSON {"tenant_id", "phone"}，TTL=INVITE_TTL_SECONDS

    为什么 seat_limit 在 invite 时校验而非 accept 时:
      若在 accept 时才校验，可能发出多份邀请但只有部分能兑现，体验差且易纠纷；
      invite 时校验保证发出去的邀请一定能兑现（除非 token 过期）。

    Args:
        tenant_id: 租户 ID
        phone: 被邀请者手机号
    Returns:
        {"invite_token", "expires_in"}
    Raises:
        TenantNotFound, SeatLimitExceeded, TenantError(手机号格式)
    """
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    try:
        validate_phone(phone)
    except ValueError as e:
        raise TenantError(str(e)) from e

    current = tenant_crud.count_members(db, tenant_id)
    if current >= tenant.seat_limit:
        raise SeatLimitExceeded(
            f"已达座席上限（{tenant.seat_limit}），无法邀请更多成员"
        )

    token = generate_random_token(24)
    payload = json.dumps({"tenant_id": tenant_id, "phone": phone})
    redis_module.get_redis().set(_invite_key(token), payload, ex=INVITE_TTL_SECONDS)
    invite_url = f"{settings.invite_link_base}?{urlencode({'token': token})}"
    return {
        "invite_token": token,
        "invite_url": invite_url,
        "expires_in": INVITE_TTL_SECONDS,
    }


def _load_invite(invite_token: str) -> dict:
    """读取一次性邀请。无效、过期或损坏的令牌使用同一条错误。"""
    raw = redis_module.get_redis().get(_invite_key(invite_token))
    if not raw:
        raise InvalidInviteToken("邀请链接无效或已过期")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise InvalidInviteToken("邀请链接无效或已过期")
    if data.get("tenant_id") is None:
        raise InvalidInviteToken("邀请链接无效或已过期")
    return data


def _invite_identity(
    db: Session,
    data: dict,
    phone: str | None,
    password: str | None,
    current_user_id: int | None,
):
    """确认接受人就是邀请绑定的手机号。已登录用户直接复用账号。"""
    if current_user_id is not None:
        user = user_crud.get_by_id(db, current_user_id)
        if user is None or data.get("phone") != user.phone:
            raise InvalidInviteToken("邀请链接无效或已过期")
        return user, user.phone
    if not phone or not password or data.get("phone") != phone:
        raise InvalidInviteToken("邀请链接无效或已过期")
    try:
        validate_phone(phone)
        validate_password_strength(password)
    except ValueError as exc:
        raise TenantError(str(exc)) from exc
    return None, phone


def _ensure_invite_capacity(db: Session, tenant_id: int) -> None:
    """接受时再次检查席位，避免并发加入超过上限。"""
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise InvalidInviteToken("邀请链接无效或已过期")
    if tenant_crud.count_members(db, tenant_id) >= tenant.seat_limit:
        raise SeatLimitExceeded(f"已达座席上限（{tenant.seat_limit}），无法加入")


def _join_invited_member(
    db: Session,
    known_user,
    current_user_id: int | None,
    phone: str,
    password: str | None,
    tenant_id: int,
):
    """在单个事务里复用或创建用户，并加入租户。失败整体回滚。"""
    try:
        user = known_user if current_user_id is not None else user_crud.get_by_phone(db, phone)
        if user is None and password is not None:
            user = user_crud.create(
                db, phone=phone, password_hash=hash_password(password), role_default="agent"
            )
        existing = tenant_crud.get_member(db, tenant_id=tenant_id, user_id=user.id)
        if existing is not None:
            raise InvalidInviteToken("该用户已加入此租户")
        tenant_crud.add_member(db, tenant_id=tenant_id, user_id=user.id, role="agent")
        db.commit()
        return user
    except InvalidInviteToken:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def accept_invite(
    db: Session,
    invite_token: str,
    phone: str | None = None,
    password: str | None = None,
    current_user_id: int | None = None,
) -> dict:
    """被邀请者接受邀请：校验 token -> 创建 user + member(role=agent)。

    流程:
      1. 从 Redis 读 invite:{token}，不存在/过期 -> InvalidInviteToken
      2. 校验接受方手机号 == token 绑定手机号（防 A 用 B 的邀请）
      3. 校验密码强度
      4. 单事务建 user + member(role=agent)，任一失败 rollback
      5. DEL token 一次性失效

    设计决策:
      - token 一次性: accept 成功后立即 DEL，防重放
      - 手机号不匹配 -> InvalidInviteToken（不暴露 token 是否有效，防探测）
      - user 已存在（跨租户）则复用，只加 member 关联
      - 单事务保证 user + member 原子性，无孤儿

    Args:
        invite_token: 邀请 token
        phone: 被邀请者手机号（须与 token 绑定一致）
        password: 登录密码
    Returns:
        {"user_id", "tenant_id", "role"}
    Raises:
        InvalidInviteToken: token 无效/过期/手机号不匹配
        TenantError: 密码强度不足
    """
    data = _load_invite(invite_token)
    known_user, phone = _invite_identity(db, data, phone, password, current_user_id)
    tenant_id = int(data["tenant_id"])
    _ensure_invite_capacity(db, tenant_id)
    user = _join_invited_member(db, known_user, current_user_id, phone, password, tenant_id)

    # 接受成功 → 一次性失效
    redis_module.get_redis().delete(_invite_key(invite_token))
    from app.services.auth_service import issue_token_pair

    access_token, refresh_token, _ = issue_token_pair(
        db, user_id=user.id, tenant_id=tenant_id, role="agent"
    )
    db.commit()
    return {
        "user_id": user.id,
        "tenant_id": tenant_id,
        "role": "agent",
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


# ────────── 套餐升级（PRD §3.9）──────────
def get_plan_catalog(db: Session, tenant_id: int) -> dict:
    """返回套餐矩阵及租户当前、待议价套餐。"""

    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    plans = []
    for plan_type, definition in PLAN_CATALOG.items():
        plans.append({
            "plan_type": plan_type,
            **{key: value for key, value in definition.items() if key != "rank"},
        })
    return {
        "current_plan": tenant.plan_type,
        "pending_plan": tenant.pending_plan_type,
        "plans": plans,
    }


def _validated_upgrade(tenant, target_plan: str) -> tuple[str, str]:
    """确认这是一次高于当前等级、且没有待处理申请的升级。"""
    target = target_plan.strip().lower()
    if target not in PLAN_CATALOG or target == "trial":
        raise InvalidPlanChange("目标套餐无效，仅支持升级至基础版、专业版或企业版")
    current = tenant.plan_type if tenant.plan_type in PLAN_CATALOG else "trial"
    if tenant.pending_plan_type:
        raise InvalidPlanChange("已有套餐变更申请正在处理中")
    if PLAN_CATALOG[target]["rank"] <= PLAN_CATALOG[current]["rank"]:
        raise InvalidPlanChange("仅支持升级到高于当前等级的套餐")
    return target, current


def _plan_change_payload(tenant, current: str, pending, status: str, quota) -> dict:
    return {
        "tenant_id": tenant.id,
        "current_plan": current,
        "pending_plan": pending,
        "change_status": status,
        "seat_limit": tenant.seat_limit,
        "conversation_quota": quota,
    }


def change_plan(db: Session, tenant_id: int, target_plan: str, *, user_id: int) -> dict:
    """升级租户套餐并联动席位与月配额（PRD §3.9）。

    基础版和专业版升级立即生效；企业版仅登记待人工议价，当前套餐继续服务。
    本接口只处理升级，不允许重复提交、降级或回到试用版。
    """

    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    target, current = _validated_upgrade(tenant, target_plan)

    if target == "enterprise":
        tenant.pending_plan_type = target
        log_audit(
            db, tenant_id=tenant_id, user_id=user_id, action="plan_upgrade_requested",
            target_type="tenant", target_id=tenant_id,
            detail=f"from={current};target=enterprise;status=pending_negotiation",
        )
        db.commit()
        return _plan_change_payload(
            tenant, current, target, "pending_negotiation",
            tenant.quota_conversations_per_month or PLAN_CATALOG[current]["conversation_quota"],
        )

    definition = PLAN_CATALOG[target]
    tenant.plan_type = target
    tenant.pending_plan_type = None
    tenant.seat_limit = definition["seat_limit"]
    tenant.quota_conversations_per_month = definition["conversation_quota"]
    tenant.status = "active"
    redis_module.get_redis().delete(f"billing:degraded:{tenant_id}")
    log_audit(
        db, tenant_id=tenant_id, user_id=user_id, action="plan_upgraded",
        target_type="tenant", target_id=tenant_id,
        detail=(
            f"from={current};to={target};seat_limit={definition['seat_limit']};"
            f"conversation_quota={definition['conversation_quota']}"
        ),
    )
    db.commit()
    return _plan_change_payload(
        tenant, tenant.plan_type, None, "effective", tenant.quota_conversations_per_month,
    )


# ────────── 企业版审批（PRD §3.1、§3.9）──────────
def approve_enterprise(db: Session, tenant_id: int) -> dict:
    """审批企业版租户: pending_review -> active。

    流程:
      1. 查租户 -> 不存在 -> TenantNotFound
      2. 校验 status == "pending_review" -> 否则 TenantNotPending
      3. status 改为 "active" -> commit

    设计决策:
      仅 pending_review 状态可审批，防止重复审批或审批已激活租户。
      审批后租户可用全部功能（seat_limit 在注册时预设 10，可后续调整）。

    Args:
        tenant_id: 租户 ID
    Returns:
        {"tenant_id", "status"}
    Raises:
        TenantNotFound: 租户不存在
        TenantNotPending: 租户非待审批状态
    """
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if tenant.status != "pending_review" and tenant.pending_plan_type != "enterprise":
        raise TenantNotPending(f"租户当前状态为 {tenant.status}，非待审批")
    tenant.plan_type = "enterprise"
    tenant.pending_plan_type = None
    tenant.seat_limit = PLAN_CATALOG["enterprise"]["seat_limit"]
    tenant.quota_conversations_per_month = PLAN_CATALOG["enterprise"]["conversation_quota"]
    tenant.status = "active"
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="enterprise_approve",
        target_type="tenant", target_id=tenant_id, detail="platform ops approval",
    )
    db.commit()
    return {"tenant_id": tenant_id, "status": "active"}


# ────────── Widget 品牌配置（PRD §3.8）──────────
def update_widget_config(
    db: Session, tenant_id: int, *,
    theme_color: str | None = None,
    logo_url: str | None = None,
    welcome_text: str | None = None,
) -> dict:
    """更新租户 Widget 品牌配置: 仅更新非 None 字段（增量），commit 后返回完整配置。

    分层收口（M11）: API 层不再直接 crud+commit。

    Args:
        tenant_id: 租户 ID
        theme_color/logo_url/welcome_text: 增量更新项，传 None 保持原值
    Returns:
        {"tenant_id", "theme_color", "logo_url", "welcome_text"}
    Raises:
        TenantNotFound: 租户不存在
    """
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    # /: 品牌字段写入校验 —— 主题色仅标准十六进制；
    # logo 与读取侧共用同一套严格校验（is_valid_absolute_logo_url）。
    # 明确提交空串 = 清除旧 Logo（区别于不提交字段保持原值）。
    if theme_color is not None and not _HEX_COLOR_RE.match(theme_color):
        raise TenantError("theme_color 必须是标准十六进制颜色（如 #1890ff）")
    from app.services.widget_service import is_valid_absolute_logo_url
    if logo_url is not None and logo_url and not is_valid_absolute_logo_url(logo_url):
        raise TenantError("logo_url 只允许空值或绝对 http/https URL（不带凭据/query/fragment）")
    # 空串 = 明确清除。crud.update_widget 只更新非 None 字段（None 语义=保持原值），
    # 因此清除需在此显式置 None，且传给 crud 的也必须转 None（空串会被当成"提交空值"写回）。
    if logo_url == "":
        tenant.widget_logo_url = None
    tenant_crud.update_widget(
        db, tenant,
        theme_color=theme_color,
        logo_url=None if logo_url == "" else logo_url,
        welcome_text=welcome_text,
    )
    db.commit()
    return {
        "tenant_id": tenant.id,
        "theme_color": tenant.widget_theme_color,
        "logo_url": tenant.widget_logo_url,
        "welcome_text": tenant.widget_welcome_text,
    }


# ────────── 退款审批阈值（PRD §3.6 规则配置）──────────
def get_refund_threshold(db: Session, tenant_id: int) -> dict:
    """查询租户退款审批阈值（当前生效值）。"""
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    return {
        "tenant_id": tenant.id,
        "refund_approval_threshold": float(tenant.refund_approval_threshold or 0),
    }


def update_refund_threshold(db: Session, tenant_id: int, threshold: float) -> dict:
    """更新租户退款审批阈值：超过该金额的退款需管理员二次审批（PRD 3.6）。

    校验: 0 < threshold；小数位不超过 2 位（金额精度与 orders/refund_approvals 一致）。
    """
    from decimal import Decimal, InvalidOperation

    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise TenantError("退款审批阈值必须是数值")
    try:
        dec = Decimal(str(threshold))
        if dec <= 0:
            raise TenantError("退款审批阈值必须大于 0")
        if dec.as_tuple().exponent < -2:
            raise TenantError("退款审批阈值小数位不能超过 2 位")
    except InvalidOperation:
        raise TenantError("退款审批阈值格式错误")

    tenant.refund_approval_threshold = dec
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="refund_threshold_update",
        target_type="tenant", target_id=tenant_id,
        detail=f"refund_approval_threshold={dec}",
    )
    db.commit()
    return {
        "tenant_id": tenant.id,
        "refund_approval_threshold": float(tenant.refund_approval_threshold),
    }


# ────────── 租户注销与数据导出（PRD §8.5）──────────
def _cancellation_now(now: datetime | None = None) -> datetime:
    """统一返回 naive UTC，匹配数据库 DateTime 字段。"""

    value = now or datetime.now(timezone.utc)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _cancellation_status_payload(tenant: Tenant, *, now: datetime | None = None) -> dict:
    current = _cancellation_now(now)
    pending = tenant.status == "pending_deletion" and tenant.scheduled_deletion_at is not None
    due = tenant.scheduled_deletion_at if pending else None
    seconds = (due - current).total_seconds() if due is not None else None
    return {
        "tenant_id": tenant.id,
        "status": tenant.status,
        "deletion_pending": pending,
        "requested_at": tenant.cancellation_requested_at.isoformat() + "Z"
        if tenant.cancellation_requested_at else None,
        "scheduled_deletion_at": due.isoformat() + "Z" if due else None,
        "days_remaining": max(0, math.ceil(seconds / 86400)) if seconds is not None else None,
        "can_revoke": bool(pending and seconds is not None and seconds > 0),
        "export_available": True,
    }


def get_tenant_cancellation_status(
    db: Session, tenant_id: int, *, now: datetime | None = None,
) -> dict:
    """查询当前租户注销状态；等待期内继续允许管理员导出与撤销。"""

    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    return _cancellation_status_payload(tenant, now=now)


def request_tenant_cancellation(
    db: Session,
    tenant_id: int,
    *,
    user_id: int,
    now: datetime | None = None,
) -> dict:
    """管理员申请注销：立即进入仅导出窗口，并计划 30 天后物理删除。"""

    current = _cancellation_now(now)
    tenant = (
        db.query(Tenant)
        .filter(Tenant.id == tenant_id)
        .with_for_update()
        .first()
    )
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if tenant.status == "pending_deletion" or tenant.cancellation_requested_at is not None:
        raise TenantCancellationConflict("租户已处于注销等待期，请勿重复申请")
    if tenant.status == "closed":
        raise TenantCancellationConflict("租户已注销")

    tenant.cancellation_previous_status = tenant.status
    tenant.cancellation_requested_at = current
    tenant.scheduled_deletion_at = current + timedelta(
        days=settings.tenant_cancellation_retention_days
    )
    tenant.status = "pending_deletion"
    log_audit(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action="tenant_cancellation_requested",
        target_type="tenant",
        target_id=tenant_id,
        detail=f"scheduled_deletion_at={tenant.scheduled_deletion_at.isoformat()}Z",
    )
    db.commit()
    db.refresh(tenant)
    return _cancellation_status_payload(tenant, now=current)


def revoke_tenant_cancellation(
    db: Session,
    tenant_id: int,
    *,
    user_id: int,
    now: datetime | None = None,
) -> dict:
    """在 30 天窗口结束前撤销注销，并精确恢复申请前租户状态。"""

    current = _cancellation_now(now)
    tenant = (
        db.query(Tenant)
        .filter(Tenant.id == tenant_id)
        .with_for_update()
        .first()
    )
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if tenant.status != "pending_deletion" or tenant.scheduled_deletion_at is None:
        raise TenantCancellationConflict("租户没有待撤销的注销申请")
    if tenant.scheduled_deletion_at <= current:
        raise TenantCancellationConflict("注销撤销窗口已结束，数据正在等待物理删除")

    previous_status = tenant.cancellation_previous_status
    if previous_status not in {"active", "readonly", "pending_review"}:
        previous_status = "active"
    tenant.status = previous_status
    tenant.cancellation_requested_at = None
    tenant.scheduled_deletion_at = None
    tenant.cancellation_previous_status = None
    log_audit(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action="tenant_cancellation_revoked",
        target_type="tenant",
        target_id=tenant_id,
        detail=f"restored_status={previous_status}",
    )
    db.commit()
    db.refresh(tenant)
    return _cancellation_status_payload(tenant, now=current)


def _csv_text(header: list[str], rows: list[list]) -> str:
    """用和 csv.writer 默认一致的换行写出表头和数据。"""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _tenant_rows(db: Session, model, tenant_id: int) -> list:
    """读取某个业务表中属于该租户的全部记录。"""
    from sqlalchemy import select

    return db.execute(select(model).where(model.tenant_id == tenant_id)).scalars().all()


def _add_documents_csv(add_csv, db: Session, tenant_id: int) -> None:
    """写入知识库文档表。"""
    from app.models.knowledge_document import KnowledgeDocument

    docs = _tenant_rows(db, KnowledgeDocument, tenant_id)
    add_csv(
        "knowledge_documents.csv",
        ["doc_id", "filename", "file_type", "file_size", "status", "version",
         "is_latest", "chunk_count", "expires_at", "quality_status", "uploaded_at"],
        [[d.id, d.filename, d.file_type, d.file_size, d.status, d.version,
          d.is_latest, d.chunk_count, d.expires_at, d.quality_status, d.created_at] for d in docs],
    )


def _add_sessions_csv(add_csv, db: Session, tenant_id: int) -> list:
    """写入会话表，并返回会话记录供消息导出使用。"""
    from app.models.session import Session as SessionModel

    sessions = _tenant_rows(db, SessionModel, tenant_id)
    add_csv(
        "sessions.csv",
        ["session_id", "status", "intent", "confidence", "sentiment", "end_user_id", "created_at", "updated_at"],
        [[s.id, s.status, s.intent, s.confidence, s.sentiment, s.end_user_id, s.created_at, s.updated_at]
         for s in sessions],
    )
    return sessions


def _add_messages_csv(add_csv, db: Session, sessions: list) -> None:
    """按创建时间写入这些会话下的消息。没有会话时不查询。"""
    from app.models.message import Message
    from sqlalchemy import select

    session_ids = [s.id for s in sessions]
    messages = (
        db.execute(
            select(Message).where(Message.session_id.in_(session_ids)).order_by(Message.created_at)
        ).scalars().all()
        if session_ids else []
    )
    add_csv(
        "messages.csv",
        ["message_id", "session_id", "role", "content", "created_at"],
        [[m.id, m.session_id, m.role, m.content, m.created_at] for m in messages],
    )


def _add_tickets_csv(add_csv, db: Session, tenant_id: int) -> None:
    """写入工单表。"""
    from app.models.ticket import Ticket

    tickets = _tenant_rows(db, Ticket, tenant_id)
    add_csv(
        "tickets.csv",
        ["ticket_id", "ticket_no", "title", "status", "priority", "assignee_id", "category", "created_at"],
        [[t.id, t.ticket_no, t.title, t.status, t.priority, t.assignee_id, t.category, t.created_at]
         for t in tickets],
    )


def _add_orders_csv(add_csv, db: Session, tenant_id: int) -> None:
    """写入订单表。"""
    from app.models.order import Order

    orders = _tenant_rows(db, Order, tenant_id)
    add_csv(
        "orders.csv",
        ["order_id", "order_no", "amount", "paid_amount", "status", "tracking_no", "created_at", "updated_at"],
        [[o.id, o.order_no, o.amount, o.paid_amount, o.status, o.tracking_no, o.created_at, o.updated_at]
         for o in orders],
    )


def _add_refunds_csv(add_csv, db: Session, tenant_id: int) -> None:
    """写入属于该租户订单的退款审批。"""
    from app.models.order import Order
    from app.models.refund_approval import RefundApproval
    from sqlalchemy import select

    approvals = db.execute(
        select(RefundApproval)
        .join(Order, Order.id == RefundApproval.order_id)
        .where(Order.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "refund_approvals.csv",
        ["approval_id", "order_id", "amount", "reason", "status", "approver_id", "pre_status", "created_at"],
        [[a.id, a.order_id, a.amount, a.reason, a.status, a.approver_id, a.pre_status, a.created_at]
         for a in approvals],
    )


def _add_invoices_csv(add_csv, db: Session, tenant_id: int) -> None:
    """写入账单表。"""
    from app.models.billing_invoice import BillingInvoice

    invoices = _tenant_rows(db, BillingInvoice, tenant_id)
    add_csv(
        "billing_invoices.csv",
        ["invoice_id", "period", "base_fee", "overage_fee", "status", "payment_trade_no", "paid_at", "created_at"],
        [[i.id, i.period, i.base_fee, i.overage_fee, i.status, i.payment_trade_no, i.paid_at, i.created_at]
         for i in invoices],
    )


def _add_usage_csv(add_csv, db: Session, tenant_id: int) -> None:
    """写入用量记录表。"""
    from app.models.usage_record import UsageRecord

    usage = _tenant_rows(db, UsageRecord, tenant_id)
    add_csv(
        "usage_records.csv",
        ["record_id", "date", "conversation_count", "is_overage", "created_at"],
        [[u.id, u.date, u.conversation_count, u.is_overage, u.created_at] for u in usage],
    )


def export_tenant_data(db: Session, tenant_id: int) -> tuple[bytes, str]:
    """导出租户核心业务数据（CSV 打包为 ZIP）。

    覆盖: 知识库文档、会话、消息、工单、订单、退款审批、账单、用量记录。
    管理员可在正常服务期或注销后的 30 天等待期内下载该归档。
    """
    import io
    import zipfile
    from datetime import datetime, timezone

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)

    def add_csv(name: str, header: list[str], rows: list[list]) -> None:
        zf.writestr(name, _csv_text(header, rows))

    _add_documents_csv(add_csv, db, tenant_id)
    sessions = _add_sessions_csv(add_csv, db, tenant_id)
    _add_messages_csv(add_csv, db, sessions)
    _add_tickets_csv(add_csv, db, tenant_id)
    _add_orders_csv(add_csv, db, tenant_id)
    _add_refunds_csv(add_csv, db, tenant_id)
    _add_invoices_csv(add_csv, db, tenant_id)
    _add_usage_csv(add_csv, db, tenant_id)

    zf.close()
    filename = f"zhihub-tenant-{tenant_id}-export-{datetime.now(timezone.utc):%Y%m%d}.zip"
    return buf.getvalue(), filename


# ────────── 规格 014 Widget 公共通道：API Key / 域名白名单 ──────────
def generate_widget_api_key(db: Session, tenant_id: int) -> dict:
    """生成/轮换 Widget API Key：仅存储 SHA-256 哈希，原始 Key 只返回一次。"""
    import hashlib
    import secrets

    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    raw_key = secrets.token_urlsafe(32)
    tenant.widget_api_key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    db.commit()
    return {"api_key": raw_key}


def set_widget_allowed_domains(db: Session, tenant_id: int, domains: list[str]) -> dict:
    """设置 Widget 域名白名单（逗号分隔存储；空列表=仅开发环境 localhost 可用）。

    : 仅接受规范 hostname / IPv4 / localhost；带 scheme、路径、query、
    fragment、端口或凭据的条目直接拒绝（TenantError -> 422）。
    """
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in domains:
        d = (raw or "").strip().lower()
        if not d:
            continue
        # 仅精确 "*." 前缀移除（兼容 *.example.com 写法，存父域子域自动匹配）；
        # 不再用 lstrip("*.")（会吞掉 **example.com 的多余星号）。
        if d.startswith("*."):
            d = d[2:]
        if not _DOMAIN_ENTRY_RE.match(d):
            raise TenantError(f"域名白名单条目无效（仅接受 hostname/IP/localhost，不带 scheme/路径/query）: {raw}")
        if d not in seen:  # 去重（保序）
            seen.add(d)
            cleaned.append(d)
    tenant.widget_allowed_domains = ",".join(cleaned)
    db.commit()
    return {"allowed_domains": cleaned}
