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
import re
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
from app.services.audit_service import log_audit
from app.utils.validators import validate_password_strength, validate_phone


# 配置向导步骤（PRD §3.1）：basic → knowledge → mcp → team → done，有序推进
WIZARD_STEPS = ["basic", "knowledge", "mcp", "team", "done"]

# 邀请 token 有效期：7 天（PRD §3.1 邀请链接带过期）
INVITE_TTL_SECONDS = 7 * 24 * 60 * 60


# ────────── 业务异常（API 层映射到 HTTP 状态码）──────────
class TenantError(Exception):
    """租户业务异常基类。"""


class TenantNotFound(TenantError):
    """租户不存在 -> 404。"""


class WizardAlreadyDone(TenantError):
    """向导已完成，不可再推进 -> 409。"""


class SeatLimitExceeded(TenantError):
    """租户成员数已达 seat_limit -> 409。"""


class InvalidInviteToken(TenantError):
    """邀请 token 无效/过期/手机号不匹配 -> 410。"""


class TenantNotPending(TenantError):
    """租户非待审批状态 -> 409（PRD 3.1 #17 企业版审批）。"""


# ────────── 配置向导 ──────────
class TenantInactive(TenantError):
    """租户状态不允许操作（readonly/closed/pending_review）-> 403"""


# 不允许发起新会话的租户状态（PRD §3.1：试用到期只读/欠费只读/已注销/待审批）
BLOCKED_NEW_SESSION_STATUSES = {"readonly", "closed", "pending_review"}


def require_active_tenant(db: Session, tenant_id: int):
    """租户状态门禁：仅 active 允许发起新会话。"""
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise TenantNotFound("租户不存在")
    if tenant.status in BLOCKED_NEW_SESSION_STATUSES:
        raise TenantInactive(f"租户当前状态为 {tenant.status}，无法发起新会话")
    return tenant


def require_message_allowed_tenant(db: Session, tenant_id: int):
    """消息入口门禁：closed 拒绝继续；readonly 允许进行中会话走完（PRD 3.1 降级语义）。"""
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        # 会话存在但租户行缺失（脏数据/测试夹具）：放行并告警，避免主链路 500
        import logging
        logging.getLogger(__name__).warning(
            f"require_message_allowed_tenant: tenant {tenant_id} not found, allowing"
        )
        return None
    if tenant.status == "closed":
        raise TenantInactive("租户已注销，无法继续会话")
    return tenant


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
    return {
        "current_step": tenant.wizard_step,
        "completed": tenant.wizard_step == WIZARD_STEPS[-1],
        "total_steps": len(WIZARD_STEPS),
        "steps": list(WIZARD_STEPS),
    }


def advance_wizard(db: Session, tenant_id: int) -> dict:
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
    raw = redis_module.get_redis().get(_invite_key(invite_token))
    if not raw:
        raise InvalidInviteToken("邀请链接无效或已过期")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise InvalidInviteToken("邀请链接无效或已过期")

    if data.get("tenant_id") is None:
        raise InvalidInviteToken("邀请链接无效或已过期")

    if current_user_id is not None:
        user = user_crud.get_by_id(db, current_user_id)
        if user is None or data.get("phone") != user.phone:
            raise InvalidInviteToken("邀请链接无效或已过期")
        phone = user.phone
    else:
        if not phone or not password or data.get("phone") != phone:
            raise InvalidInviteToken("邀请链接无效或已过期")
        try:
            validate_phone(phone)
            validate_password_strength(password)
        except ValueError as e:
            raise TenantError(str(e)) from e

    tenant_id = int(data["tenant_id"])

    # 重新校验 seat_limit（防并发 accept 超限；invite 时校验不能覆盖 accept 并发）
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise InvalidInviteToken("邀请链接无效或已过期")
    if tenant_crud.count_members(db, tenant_id) >= tenant.seat_limit:
        raise SeatLimitExceeded(f"已达座席上限（{tenant.seat_limit}），无法加入")

    try:
        # user 已存在（跨租户）则复用，否则新建
        user = user_crud.get_by_phone(db, phone) if current_user_id is None else user
        if user is None and password is not None:
            user = user_crud.create(
                db, phone=phone, password_hash=hash_password(password), role_default="agent"
            )
        # 防重复加入（同租户已存在 member → 视为已接受过）
        existing = tenant_crud.get_member(db, tenant_id=tenant_id, user_id=user.id)
        if existing is not None:
            raise InvalidInviteToken("该用户已加入此租户")
        tenant_crud.add_member(db, tenant_id=tenant_id, user_id=user.id, role="agent")
        db.commit()
    except InvalidInviteToken:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

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


# ────────── 企业版审批（PRD §3.1 #17）──────────
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
    if tenant.status != "pending_review":
        raise TenantNotPending(f"租户当前状态为 {tenant.status}，非待审批")
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


# ────────── 租户数据导出（PRD §8.5 最小实现）──────────
def export_tenant_data(db: Session, tenant_id: int) -> tuple[bytes, str]:
    """导出租户核心业务数据（CSV 打包为 ZIP）。

    覆盖: 知识库文档、会话、消息、工单、订单、退款审批、账单、用量记录。
    本版本为管理员手动导出（PRD 8.5 的最小实现）；注销申请、30 天窗口与物理删除
    列入后续迭代（见 PRD 9.3）。
    """
    import csv
    import io
    import zipfile
    from datetime import datetime, timezone

    from app.models.billing_invoice import BillingInvoice
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.message import Message
    from app.models.order import Order
    from app.models.refund_approval import RefundApproval
    from app.models.session import Session as SessionModel
    from app.models.ticket import Ticket
    from app.models.usage_record import UsageRecord
    from sqlalchemy import select

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)

    def add_csv(name: str, header: list[str], rows: list[list]) -> None:
        sio = io.StringIO()
        writer = csv.writer(sio)
        writer.writerow(header)
        writer.writerows(rows)
        zf.writestr(name, sio.getvalue())

    docs = db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "knowledge_documents.csv",
        ["doc_id", "filename", "file_type", "file_size", "status", "version",
         "is_latest", "chunk_count", "expires_at", "quality_status", "uploaded_at"],
        [[d.id, d.filename, d.file_type, d.file_size, d.status, d.version,
          d.is_latest, d.chunk_count, d.expires_at, d.quality_status, d.created_at] for d in docs],
    )

    sessions = db.execute(
        select(SessionModel).where(SessionModel.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "sessions.csv",
        ["session_id", "status", "intent", "confidence", "sentiment", "end_user_id", "created_at", "updated_at"],
        [[s.id, s.status, s.intent, s.confidence, s.sentiment, s.end_user_id, s.created_at, s.updated_at]
         for s in sessions],
    )

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

    tickets = db.execute(
        select(Ticket).where(Ticket.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "tickets.csv",
        ["ticket_id", "ticket_no", "title", "status", "priority", "assignee_id", "category", "created_at"],
        [[t.id, t.ticket_no, t.title, t.status, t.priority, t.assignee_id, t.category, t.created_at]
         for t in tickets],
    )

    orders = db.execute(
        select(Order).where(Order.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "orders.csv",
        ["order_id", "order_no", "amount", "paid_amount", "status", "tracking_no", "created_at", "updated_at"],
        [[o.id, o.order_no, o.amount, o.paid_amount, o.status, o.tracking_no, o.created_at, o.updated_at]
         for o in orders],
    )

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

    invoices = db.execute(
        select(BillingInvoice).where(BillingInvoice.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "billing_invoices.csv",
        ["invoice_id", "period", "base_fee", "overage_fee", "status", "payment_trade_no", "paid_at", "created_at"],
        [[i.id, i.period, i.base_fee, i.overage_fee, i.status, i.payment_trade_no, i.paid_at, i.created_at]
         for i in invoices],
    )

    usage = db.execute(
        select(UsageRecord).where(UsageRecord.tenant_id == tenant_id)
    ).scalars().all()
    add_csv(
        "usage_records.csv",
        ["record_id", "date", "conversation_count", "is_overage", "created_at"],
        [[u.id, u.date, u.conversation_count, u.is_overage, u.created_at] for u in usage],
    )

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
