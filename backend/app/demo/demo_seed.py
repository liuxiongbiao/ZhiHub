"""
backend/app/demo/demo_seed.py

 / : 可重复执行的电商演示租户 seed —— "ZhiHub 智选商城"。

职责:
  - 一键创建演示租户（管理员 + 2 客服）、演示知识、演示订单/工单/退款/会话/
    快捷回复/账单/通知/审计样例
  - 幂等：重复执行不创建重复用户/成员/租户，summary 来自数据库真实查询
  - reset 采用"归档"语义：关闭旧演示实例并释放固定手机号，新实例由下次 create
    建立 —— 不删除/篡改 refund_approval_events 等不可变审计证据
  - 环境限制：仅 development/test/demo 环境可执行，production 直接拒绝
  - 租户所有权：tenants.is_demo 持久标记（d34），不依赖可碰撞的 name

关键设计:
  - 演示密码不硬编码：优先命令行参数，其次环境变量 DEMO_ADMIN_PASSWORD /
    DEMO_AGENT_PASSWORD；仅在需要新建账号时才生成一次性随机密码并打印
    （已有账号且未请求 reset-passwords 时绝不生成/打印无效随机密码）
  - 预置退款走真实状态机 order_service.request_refund / approve_refund：
    订单状态、approval.status、pre_status、requested 事件相互一致；
    每订单最多一个活动退款申请
  - 知识必须走现有上传+异步处理流程，禁止直接置 ready 或写死假 embedding；
    处理失败/超时置 failed + error_message
  - 单号冲突保护：演示单号被其他租户占用时报错回滚，绝不认领/修改他人数据
  - 业务数据单事务写入，失败整体回滚；错误信息不含任何密钥

用法:
  python -m app.demo.demo_seed create [--admin-password X] [--agent-password Y]
  python -m app.demo.demo_seed reset --tenant-id N --yes
"""

from __future__ import annotations

import argparse
import io
import os
import re
import secrets
import sys
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings

DEMO_TENANT_NAME = "ZhiHub 智选商城"
DEMO_ADMIN_PHONE = "13900000000"
DEMO_AGENT1_PHONE = "13900000001"
DEMO_AGENT2_PHONE = "13900000002"
# 演示订单/工单绑定的终端客户手机号（剧本按后 4 位做双因子校验）
DEMO_CUSTOMER_PHONE = "13911112222"

_ALLOWED_ENVS = ("development", "test", "demo")

# 合法中国手机号（占位号也必须满足）
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")

# 固定演示手机号 → 租户内预期角色（身份集合，密码重置/归档/认领共用）
_DEMO_PHONE_ROLES: dict[str, str] = {
    DEMO_ADMIN_PHONE: "admin",
    DEMO_AGENT1_PHONE: "agent",
    DEMO_AGENT2_PHONE: "agent",
}


class DemoSeedError(Exception):
    """demo seed 业务异常基类。"""


class DemoSeedEnvError(DemoSeedError):
    """环境不允许执行 demo seed（如 production）。"""


class DemoSeedConflictError(DemoSeedError):
    """数据冲突（单号/固定手机号被他租户占用 / 非演示租户 / 身份不匹配）。"""


class DemoSeedKnowledgeError(DemoSeedError):
    """演示知识上传/处理失败（不得伪装 ready）。"""


# ─────────────────────────────────────────────────────────────
# 演示知识（7 份；特殊赔偿问题故意不建文档，用于触发转人工）
# ─────────────────────────────────────────────────────────────
DEMO_KB_DOCS: dict[str, str] = {
    "商品介绍.md": """# 智选商城商品介绍

智选商城主营智能家居与数码配件。热销商品包括：智选空气净化器（899 元）、智选降噪耳机（499 元）、智选智能音箱（299 元）、智选快充头（99 元）。全部商品支持七天无理由退货，并提供全国联保。

商品页标注清晰的规格参数与售后服务说明，下单前可在线咨询人工客服。""",
    "七天无理由退货政策.md": """# 七天无理由退货政策

智选商城支持七天无理由退货：商品签收后 7 天内、商品完好（不影响二次销售，保留吊牌与外包装）即可申请退货。七天无理由退货无需任何理由，运费由买家承担（质量问题由商城承担）。

退货流程：在订单页面发起退货申请 -> 客服审核 -> 按指引寄回商品 -> 仓库验收后 1-3 个工作日原路退款。已拆封的食品、定制类商品不适用七天无理由退货。""",
    "发货时间与物流政策.md": """# 发货时间与物流政策

已支付订单通常在 24 小时内发货，工作日 16:00 前支付的订单当日发出。默认使用顺丰/中通快递，全国大部分地区 2-4 天送达。发货后系统更新物流单号，用户可在订单页面实时跟踪物流信息。

偏远地区（新疆、西藏等）配送时间可能延长至 5-7 天。如遇物流长时间未更新，可联系人工客服提交工单跟进。""",
    "商品保修政策.md": """# 商品保修政策

智选商城商品提供自签收之日起一年全国联保。保修期内非人为损坏的故障免费维修，无法维修的免费换新。人为损坏、私自拆修、进水等情况不在保修范围内。

保修申请：联系人工客服提供订单号与故障描述，客服创建售后工单并安排检测。保修运费由商城承担。""",
    "发票政策.md": """# 发票政策

智选商城支持开具电子普通发票与增值税专用发票。电子普票在支付成功后自动开具，发送至订单联系邮箱；增值税专用发票需在订单页面填写公司名称、税号、地址电话、开户行及账号，财务审核后 3-5 个工作日开具。

发票开具后可在账户中心下载或申请重开（信息有误 30 天内可申请重开）。""",
    "退款到账时间.md": """# 退款到账时间

退款审核通过后按原支付渠道退回：微信支付/支付宝退款实时到账（一般 2 小时内）；银行卡退款 1-3 个工作日到账；对公转账退款 3-5 个工作日到账。

退款金额不超过订单实付金额，原路退回至支付时使用的账户。退款进度可在订单页面查看，超过上述时限未到账可联系人工客服核查。""",
    "人工客服服务时间.md": """# 人工客服服务时间

智选商城人工客服在线时间为每天 9:00-21:00（全年无休）。在线时间内，智能助手无法解决的问题会自动转接人工客服；非服务时间可留言，客服会在次日 9:00 后按顺序处理。

紧急问题（订单支付异常、物流丢失等）可提交加急工单，客服团队会优先处理。""",
}


# ─────────────────────────────────────────────────────────────
# 演示业务数据
# ─────────────────────────────────────────────────────────────
# (订单号, 金额, 状态, 说明)
DEMO_ORDERS = [
    ("ORD-DEMO-0001", 299.0, "pending_payment", "待支付订单"),
    ("ORD-DEMO-0002", 499.0, "paid", "已支付、可发货订单"),
    ("ORD-DEMO-0003", 899.0, "shipped", "已发货、含物流单号订单"),
    ("ORD-DEMO-0004", 99.0, "completed", "已完成订单"),
    ("ORD-DEMO-0005", 299.0, "paid", "可申请退款订单（E2E eligible，无活动退款）"),
    ("ORD-DEMO-0006", 1299.0, "paid", "已进入退款审批的订单（超额需管理员终审）"),
]
DEMO_ORDER_TRACKING = {"ORD-DEMO-0003": "SF1234567890"}

# (工单号, 标题, 描述, 优先级, 状态)
DEMO_TICKETS = [
    ("TK-DEMO-0001", "商品签收后外观破损", "客户反馈签收后发现商品外观破损，需核实换货", "high", "open"),
    ("TK-DEMO-0002", "发票抬头填写错误申请重开", "客户申请重开增值税专用发票，已提交新抬头信息", "medium", "resolved"),
]

# 预置退款（真实状态机）:
# 0006: 活动退款 —— request_refund(1299) 后保持 pending（超额，管理员终审）
# 0002: 终态退款 —— request_refund(30) 后 reject（小额驳回示例，订单回退 paid）
DEMO_REFUND_ACTIVE = ("ORD-DEMO-0006", 1299.0, "demo-cid-ORD-DEMO-0006")
DEMO_REFUND_REJECTED = ("ORD-DEMO-0002", 30.0, "demo-cid-ORD-DEMO-0002")

# 演示订单/工单的创建者口径（幂等 create 时恢复）
DEMO_ORDER_CREATED_BY = "admin"
DEMO_TICKET_CREATED_BY = "staff"


def _assert_env_allowed() -> None:
    if settings.app_env not in _ALLOWED_ENVS:
        raise DemoSeedEnvError(
            f"demo seed 仅允许在 development/test/demo 环境执行，"
            f"当前 app_env={settings.app_env!r}（production 拒绝执行）"
        )


def _validate_or_raise(password: str, label: str) -> str:
    from app.utils.validators import validate_password_strength
    try:
        validate_password_strength(password)
    except ValueError as e:
        raise DemoSeedError(f"{label}不符合密码强度要求: {e}") from e
    return password


class _PasswordProvider:
    """按需解析密码：只有真正需要新建账号时才解析/生成（避免打印无效随机密码）。"""

    def __init__(self, explicit: str | None, env_key: str, label: str):
        self.explicit = explicit
        self.env_key = env_key
        self.label = label
        self._resolved: str | None = None

    def get(self) -> str:
        if self._resolved is not None:
            return self._resolved
        if self.explicit:
            self._resolved = _validate_or_raise(self.explicit, self.label)
            return self._resolved
        env_value = os.environ.get(self.env_key) or ""
        if env_value:
            self._resolved = _validate_or_raise(env_value, self.label)
            return self._resolved
        generated = secrets.token_urlsafe(9) + "!1aA"  # 随机生成，含大小写字母数字符号
        self._resolved = _validate_or_raise(generated, self.label)
        print(f"[demo] {self.label}未提供，已随机生成（仅本次显示）: {generated}")
        return self._resolved


def _find_or_create_demo_tenant(
    db: Session, admin_pw: _PasswordProvider, *, claim_legacy: bool = False
) -> tuple[int, bool]:
    """定位/创建演示租户。所有权以 is_demo 持久标记为准（name 可碰撞，仅作显示名）。

    : 固定手机号已被非演示租户用户占用时 fail-closed，绝不修改该用户。
    : legacy 认领必须显式 claim_legacy=True，且校验完整演示身份集合；
    普通 create 绝不自动认领"同名 + 固定手机号成员"的可疑租户。
    """
    from app.models.tenant import Tenant

    tenant = (
        db.query(Tenant)
        .filter(Tenant.is_demo.is_(True), Tenant.status == "active")
        .order_by(Tenant.id.desc())
        .first()
    )
    if tenant is not None:
        _assert_demo_phones_available(db)
        return tenant.id, False

    # d34 迁移兼容：is_demo 列引入前创建的旧演示实例。仅显式 claim-legacy 时认领，
    # 且要求完整演示身份集合（3 个固定手机号成员、角色精确匹配、无非 demo 混合归属）。
    if claim_legacy:
        legacy = (
            db.query(Tenant)
            .filter(Tenant.name == DEMO_TENANT_NAME, Tenant.status == "active")
            .order_by(Tenant.id.desc())
            .first()
        )
        if legacy is not None and _is_legacy_demo_instance(db, legacy.id):
            legacy.is_demo = True
            db.commit()
            return legacy.id, False

    _assert_demo_phones_available(db, allow_existing=False)

    from app.services.auth_service import register

    result = register(
        db, phone=DEMO_ADMIN_PHONE, password=admin_pw.get(),
        tenant_name=DEMO_TENANT_NAME, plan_type="enterprise",
    )
    tenant = db.query(Tenant).filter_by(id=result.tenant_id).first()
    tenant.status = "active"  # 企业版默认 pending_review：演示环境直接激活（模拟平台审批）
    tenant.is_demo = True
    db.commit()
    return tenant.id, True


def _is_legacy_demo_instance(db: Session, tenant_id: int) -> bool:
    """旧实例认领判据（ 收紧）：完整演示身份集合 + 角色精确匹配 + 无非 demo 混合归属。

    同名且仅含一个固定手机号成员的普通租户不满足判据，不得被认领。
    注意：候选租户自身在评估时还未标记 is_demo，需把它视作 demo 归属（否则自相矛盾）。
    """
    from app.crud import user as user_crud
    from app.models.tenant import Tenant
    from app.models.tenant_member import TenantMember

    demo_ids = {
        t.id for t in db.query(Tenant.id).filter(Tenant.is_demo.is_(True)).all()
    } | {tenant_id}
    for phone, expected_role in _DEMO_PHONE_ROLES.items():
        user = user_crud.get_by_phone(db, phone)
        if user is None:
            return False
        member = (
            db.query(TenantMember)
            .filter_by(tenant_id=tenant_id, user_id=user.id, role=expected_role)
            .first()
        )
        if member is None:
            return False
        members = db.query(TenantMember).filter_by(user_id=user.id).all()
        if not members or any(m.tenant_id not in demo_ids for m in members):
            return False
    return True


def _assert_demo_phones_available(db: Session, allow_existing: bool = True) -> None:
    """固定演示手机号不得被非演示租户用户占用（fail-closed）。

    : 用户只要存在任意非 demo 租户 membership（含"同时属于 demo 和普通租户"
    的混合归属）就必须 fail-closed；无任何 membership 的孤立占号用户同样拒绝。
    allow_existing=False 时（全新创建路径）任何已存在的占号用户都拒绝。
    """
    from app.crud import user as user_crud

    for phone in _DEMO_PHONE_ROLES:
        user = user_crud.get_by_phone(db, phone)
        if user is None:
            continue
        if _has_non_demo_membership(db, user.id):
            raise DemoSeedConflictError(
                f"演示固定手机号 {phone} 已被非演示租户用户占用（fail-closed，不修改该用户）"
            )
        if not allow_existing:
            raise DemoSeedConflictError(f"演示固定手机号 {phone} 已被占用（非演示实例）")


def _has_non_demo_membership(db: Session, user_id: int) -> bool:
    """用户是否存在任意非 demo 租户的 membership（混合归属检测）。

    无任何 membership 的孤立占号用户同样返回 True（无法证明其归属，fail-closed）。
    """
    from app.models.tenant import Tenant
    from app.models.tenant_member import TenantMember

    members = db.query(TenantMember).filter_by(user_id=user_id).all()
    if not members:
        return True
    demo_ids = {
        t.id for t in db.query(Tenant.id).filter(Tenant.is_demo.is_(True)).all()
    }
    return any(m.tenant_id not in demo_ids for m in members)


def _exclusive_demo_member(
    db: Session, tenant_id: int, phone: str, *, expected_role: str | None = None
):
    """返回 (user, member) 仅当：用户是 tenant_id 的成员、角色精确匹配预期、
    且不存在任何非 demo 租户 membership（排他性）。否则返回 None。"""
    from app.crud import user as user_crud
    from app.models.tenant_member import TenantMember

    role = expected_role or _DEMO_PHONE_ROLES.get(phone, "agent")
    user = user_crud.get_by_phone(db, phone)
    if user is None:
        return None
    member = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_id=user.id, role=role)
        .first()
    )
    if member is None:
        return None
    if _has_non_demo_membership(db, user.id):
        return None
    return user, member


def _ensure_demo_agents(db: Session, tenant_id: int, agent_pw: _PasswordProvider) -> None:
    """幂等创建两名演示客服（invite + accept）。占号者存在非 demo 归属时 fail-closed。"""
    from app.crud import user as user_crud
    from app.models.tenant_member import TenantMember
    from app.services.tenant_service import accept_invite, invite_member

    for phone in (DEMO_AGENT1_PHONE, DEMO_AGENT2_PHONE):
        user = user_crud.get_by_phone(db, phone)  # phone 为加密存储，必须走 crud 解密查询
        if user is not None:
            if _has_non_demo_membership(db, user.id):
                raise DemoSeedConflictError(
                    f"演示固定手机号 {phone} 已被非演示租户用户占用（含混合归属，fail-closed）"
                )
            member = db.query(TenantMember).filter_by(tenant_id=tenant_id, user_id=user.id).first()
            if member is not None:
                continue  # 已存在，幂等跳过
        invite = invite_member(db, tenant_id, phone)
        accept_invite(db, invite["invite_token"], phone=phone, password=agent_pw.get())


def _reset_demo_passwords(db: Session, tenant_id: int, admin_pw: str, agent_pw: str) -> None:
    """显式重置演示账号密码。

    : 必须先证明 tenant_id 是当前 active demo tenant，且每个账号都是该租户
    精确角色的成员、不存在任何非 demo 混合归属；任一不满足即拒绝（fail-closed）。
    """
    from app.core.crypto import phone_hash
    from app.core.security import hash_password
    from app.models.tenant import Tenant

    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if tenant is None or not tenant.is_demo or tenant.status != "active":
        raise DemoSeedConflictError(
            f"tenant_id={tenant_id} 不是当前 active 演示租户，拒绝重置演示密码"
        )

    for phone, pw in (
        (DEMO_ADMIN_PHONE, admin_pw),
        (DEMO_AGENT1_PHONE, agent_pw),
        (DEMO_AGENT2_PHONE, agent_pw),
    ):
        pair = _exclusive_demo_member(db, tenant_id, phone)
        if pair is None:
            raise DemoSeedConflictError(
                f"账号 {phone} 不是当前演示租户的预期角色成员（或存在非 demo 混合归属），拒绝重置密码"
            )
        user = pair[0]
        user.password_hash = hash_password(pw)
        user.phone = phone  # EncryptedString 自动加密（no-op 幂等）
        user.phone_hash = phone_hash(phone)
    db.commit()


def _seed_business_data(db: Session, tenant_id: int) -> dict:
    """业务数据（订单/工单/退款/会话/快捷回复/账单/用量/通知/审计），单事务。"""
    from app.models.billing_invoice import BillingInvoice
    from app.models.notification import Notification
    from app.models.order import Order
    from app.models.quick_reply import QuickReply
    from app.models.refund_approval import RefundApproval
    from app.models.session import Session
    from app.models.ticket import Ticket
    from app.models.usage_record import UsageRecord
    from app.services.audit_service import log_audit
    from app.services.order_service import OrderError, approve_refund, request_refund

    counts = {"orders": 0, "tickets": 0, "refunds": 0, "sessions": 0}

    preset_status = {ono: status for ono, _, status, _ in DEMO_ORDERS}
    preset_amounts = {ono: amount for ono, amount, _, _ in DEMO_ORDERS}

    for order_no, amount, status, _note in DEMO_ORDERS:
        existing = db.query(Order).filter_by(order_no=order_no).first()
        if existing is not None and existing.tenant_id != tenant_id:
            raise DemoSeedConflictError(
                f"演示订单号 {order_no} 已被其他租户占用，拒绝修改他人数据"
            )
        if existing is None:
            db.add(Order(
                tenant_id=tenant_id, order_no=order_no, amount=amount,
                paid_amount=amount if status != "pending_payment" else 0.0,
                status=status, phone=DEMO_CUSTOMER_PHONE, version=1,
                tracking_no=DEMO_ORDER_TRACKING.get(order_no),
                payment_trade_no=f"PAY-DEMO-{order_no}" if status != "pending_payment" else None,
                created_by=DEMO_ORDER_CREATED_BY,
            ))
        else:
            # 幂等：恢复全部剧本字段（金额/实付/状态/版本/物流/手机号/支付流水/创建者）
            existing.tenant_id = tenant_id
            existing.amount = amount
            existing.paid_amount = amount if status != "pending_payment" else 0.0
            existing.status = status
            existing.version = 1
            existing.tracking_no = DEMO_ORDER_TRACKING.get(order_no)
            existing.phone = DEMO_CUSTOMER_PHONE
            existing.payment_trade_no = (
                f"PAY-DEMO-{order_no}" if status != "pending_payment" else None
            )
            existing.created_by = DEMO_ORDER_CREATED_BY
        counts["orders"] += 1

    for ticket_no, title, description, priority, status in DEMO_TICKETS:
        existing = db.query(Ticket).filter_by(ticket_no=ticket_no).first()
        if existing is not None and existing.tenant_id != tenant_id:
            raise DemoSeedConflictError(
                f"演示工单号 {ticket_no} 已被其他租户占用，拒绝修改他人数据"
            )
        if existing is None:
            db.add(Ticket(
                tenant_id=tenant_id, ticket_no=ticket_no, title=title,
                description=description, priority=priority,
                status=status, phone=DEMO_CUSTOMER_PHONE, category="general",
                created_by=DEMO_TICKET_CREATED_BY, assignee_id=None,
            ))
        else:
            existing.status = status
            existing.title = title
            existing.description = description
            existing.priority = priority
            existing.category = "general"
            existing.phone = DEMO_CUSTOMER_PHONE
            existing.created_by = DEMO_TICKET_CREATED_BY
            existing.assignee_id = None
        counts["tickets"] += 1

    db.flush()

    # 预置退款走真实状态机（订单状态 / approval.status / pre_status / 事件相互一致）：
    # 0006 活动退款：request_refund → pending + requested 事件（超额需管理员终审）
    # 0002 终态驳回示例：request_refund → reject → rejected 事件链，订单回退 paid
    for order_no, amount, cid, decision in (
        (*DEMO_REFUND_ACTIVE, None),
        (*DEMO_REFUND_REJECTED, "rejected"),
    ):
        order = db.query(Order).filter_by(tenant_id=tenant_id, order_no=order_no).first()
        approval = db.query(RefundApproval).filter_by(order_id=order.id, client_request_id=cid).first()
        if approval is None:
            # 订单处于 paid（DEMO_ORDERS 初始状态）→ request_refund 推进到 refund_requested
            result = request_refund(
                db, tenant_id=tenant_id, order_no=order_no, amount=amount,
                reason=f"演示退款申请（{order_no}）", client_request_id=cid,
                phone=DEMO_CUSTOMER_PHONE,
            )
            db.commit()
            approval = db.query(RefundApproval).filter_by(id=result["id"]).first()
        else:
            # 幂等：审批已存在 → 订单状态与审批聚合保持一致
            if decision is None:
                order.status = "refund_requested"  # 活动退款：订单保持退款中
            else:
                order.status = "paid"  # 终态驳回语义：回退 pre_status
        if decision is not None and approval.status == "pending":
            # 终态示例：驳回 → 订单回退 pre_status（paid），审批聚合终态与事件一致
            try:
                approve_refund(db, approval_id=approval.id, decision=decision,
                               approver_id=None, tenant_id=tenant_id, role="admin")
                db.commit()
                order.status = "paid"
            except OrderError:
                db.rollback()
        counts["refunds"] += 1

    # 会话: waiting_human 1 + closed 1
    for end_user_id, status in (("eu-demo-waiting", "waiting_human"), ("eu-demo-closed", "closed")):
        sess = db.query(Session).filter_by(tenant_id=tenant_id, end_user_id=end_user_id).first()
        if sess is None:
            db.add(Session(
                tenant_id=tenant_id, status=status,
                intent="order_query" if status == "waiting_human" else "knowledge_consult",
                confidence=0.3 if status == "waiting_human" else 0.9,
                sentiment="unhappy" if status == "waiting_human" else "normal",
                end_user_id=end_user_id,
            ))
        else:
            sess.status = status
            sess.assignee_id = None
        counts["sessions"] += 1

    # 快捷回复（幂等：已存在则恢复剧本内容，而非仅按唯一键跳过）
    for content, shortcut in (
        ("您好，正在为您查询，请稍候。", "chaxun"),
        ("感谢您的反馈，我们会尽快处理。", "ganxie"),
    ):
        existing_qr = db.query(QuickReply).filter_by(tenant_id=tenant_id, shortcut=shortcut).first()
        if existing_qr is None:
            db.add(QuickReply(tenant_id=tenant_id, content=content, shortcut=shortcut))
        else:
            existing_qr.content = content

    # 账单 + 用量（幂等：已存在则恢复演示字段）
    for period, status, base, overage in (("2026-08", "unpaid", 100.0, 12.5), ("2026-07", "paid", 100.0, 0.0)):
        existing_inv = db.query(BillingInvoice).filter_by(tenant_id=tenant_id, period=period).first()
        if existing_inv is None:
            db.add(BillingInvoice(tenant_id=tenant_id, period=period, base_fee=base, overage_fee=overage, status=status))
        else:
            existing_inv.status = status
            existing_inv.base_fee = base
            existing_inv.overage_fee = overage
    from datetime import date, timedelta
    for i in range(3):
        d = date(2026, 8, 15 - i)
        existing_ur = db.query(UsageRecord).filter_by(tenant_id=tenant_id, date=d).first()
        if existing_ur is None:
            db.add(UsageRecord(tenant_id=tenant_id, date=d, conversation_count=12 + i * 7, is_overage=(i == 0)))
        else:
            existing_ur.conversation_count = 12 + i * 7
            existing_ur.is_overage = (i == 0)

    # 通知样例（幂等）
    if not db.query(Notification).filter_by(tenant_id=tenant_id, type="demo_welcome").first():
        db.add(Notification(tenant_id=tenant_id, title="欢迎使用 ZhiHub 智选商城演示环境",
                            content="演示数据已就绪，可开始演示剧本。", type="demo_welcome"))

    # 审计样例（幂等：demo_seed action 只写一次，不随重复 seed 累加）
    from app.models.audit_log import AuditLog
    if not db.query(AuditLog).filter_by(tenant_id=tenant_id, action="demo_seed").first():
        log_audit(db, tenant_id=tenant_id, user_id=None, action="demo_seed",
                  target_type="tenant", target_id=tenant_id, detail="demo data seeded")

    db.commit()
    return counts


def _upload_knowledge(db: Session, *, file, tenant_id: int, expires_days: int | None = None) -> dict:
    """真实知识上传：走 knowledge_service.upload（rabbitmq -> worker -> embedding）。"""
    from app.services.knowledge_service import upload as kb_upload
    return kb_upload(db, file=file, tenant_id=tenant_id, expires_days=expires_days)


def _wait_doc_ready(db: Session, doc_id: int, tenant_id: int, timeout_seconds: int = 120) -> str:
    """轮询文档处理状态，直到 ready/failed/超时。超时/失败时置 failed + error_message。

    注意: 文档由 celery worker 进程处理并提交。MySQL REPEATABLE READ 下当前事务的
    快照看不到 worker 的提交，因此每轮 commit() 结束旧事务、开启新快照后再读。
    """
    from app.crud import knowledge as knowledge_crud
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        db.commit()  # 刷新事务快照，可见 worker 进程的最新提交
        doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
        if doc is None:
            return "failed"
        if doc.status == "ready":
            return "ready"
        if doc.status == "failed":
            return "failed"
        time.sleep(2)
    # 超时：文档必须真正变为 failed 并记录原因（不允许悬挂 pending 伪装成功）
    doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
    if doc is not None and doc.status not in ("ready", "failed"):
        knowledge_crud.update_status(db, doc, "failed", error_message="演示知识处理超时（seed 等待超限）")
        db.commit()
    return "failed"


def _seed_knowledge(db: Session, tenant_id: int, upload_fn, wait_timeout: int) -> list[str]:
    """上传 7 份演示知识（真实处理流程）。同名已 ready 的幂等跳过；失败返回文档名列表。"""
    from app.models.knowledge_document import KnowledgeDocument

    failed: list[str] = []
    for filename, content in DEMO_KB_DOCS.items():
        existing = (
            db.query(KnowledgeDocument)
            .filter_by(tenant_id=tenant_id, filename=filename, is_latest=True)
            .first()
        )
        if existing is not None and existing.status == "ready":
            continue  # 幂等跳过
        # knowledge_service.upload 期望 FastAPI UploadFile 接口（.file/.filename）
        from starlette.datastructures import UploadFile
        file = UploadFile(file=io.BytesIO(content.encode("utf-8")), filename=filename)
        try:
            result = upload_fn(db, file=file, tenant_id=tenant_id)
            doc_id = result.get("doc_id")
            status = _wait_doc_ready(db, doc_id, tenant_id, timeout_seconds=wait_timeout) if doc_id else "failed"
            if status != "ready":
                failed.append(filename)
        except Exception:
            failed.append(filename)
    return failed


def seed(
    db: Session | None = None,
    admin_password: str | None = None,
    agent_password: str | None = None,
    upload_fn=None,
    reset_passwords: bool = False,
    wait_timeout: int = 120,
    claim_legacy: bool = False,
) -> dict:
    """创建/补齐演示租户（幂等）。返回 summary dict（来自数据库真实计数，不含密钥）。

    claim_legacy=True 时才会认领 d34 之前创建、is_demo 尚未标记的旧演示实例
    （需通过完整演示身份集合校验）；默认不认领。
    """
    _assert_env_allowed()
    from app.core.database import SessionLocal

    own_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        admin_pw = _PasswordProvider(admin_password, "DEMO_ADMIN_PASSWORD", "演示管理员密码")
        agent_pw = _PasswordProvider(agent_password, "DEMO_AGENT_PASSWORD", "演示客服密码")

        tenant_id, created = _find_or_create_demo_tenant(db, admin_pw, claim_legacy=claim_legacy)
        _ensure_demo_agents(db, tenant_id, agent_pw)
        if reset_passwords:
            # 显式重置演示账号密码（先校验 active demo tenant 归属与角色；默认幂等不动已有密码）
            _reset_demo_passwords(db, tenant_id, admin_pw.get(), agent_pw.get())
        _seed_business_data(db, tenant_id)

        failed_docs = _seed_knowledge(db, tenant_id, upload_fn or _upload_knowledge, wait_timeout)
        if failed_docs:
            raise DemoSeedKnowledgeError(
                f"演示知识处理失败（未伪装 ready）: {', '.join(failed_docs)}"
            )
        return _summary_from_db(db, tenant_id, created)
    finally:
        if own_db and db is not None:
            db.close()


def _summary_from_db(db: Session, tenant_id: int, created: bool) -> dict:
    """summary 全部来自数据库真实查询，不用循环常量计数冒充。"""
    from app.models.audit_log import AuditLog
    from app.models.billing_invoice import BillingInvoice
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.notification import Notification
    from app.models.order import Order
    from app.models.quick_reply import QuickReply
    from app.models.refund_approval import RefundApproval
    from app.models.session import Session
    from app.models.tenant import Tenant
    from app.models.tenant_member import TenantMember
    from app.models.ticket import Ticket
    from app.models.usage_record import UsageRecord

    order_ids = [o.id for o in db.query(Order).filter_by(tenant_id=tenant_id).all()]
    return {
        "tenant_id": tenant_id,
        "tenant_name": DEMO_TENANT_NAME,
        "created": created,
        # 报告全库演示实例 active / archived 数量（活动实例不累积 != 全库不累积）
        "demo_tenants_active": _count_demo_tenants(db, "active"),
        "demo_tenants_archived": _count_demo_tenants(db, "archived"),
        "admin_phone": DEMO_ADMIN_PHONE,
        "agent_phones": [DEMO_AGENT1_PHONE, DEMO_AGENT2_PHONE],
        "customer_phone": DEMO_CUSTOMER_PHONE,
        "members": db.query(TenantMember).filter_by(tenant_id=tenant_id).count(),
        "orders": db.query(Order).filter_by(tenant_id=tenant_id).count(),
        "tickets": db.query(Ticket).filter_by(tenant_id=tenant_id).count(),
        "refunds": db.query(RefundApproval).filter(RefundApproval.order_id.in_(order_ids)).count(),
        "sessions": db.query(Session).filter_by(tenant_id=tenant_id).count(),
        "quick_replies": db.query(QuickReply).filter_by(tenant_id=tenant_id).count(),
        "invoices": db.query(BillingInvoice).filter_by(tenant_id=tenant_id).count(),
        "usage_records": db.query(UsageRecord).filter_by(tenant_id=tenant_id).count(),
        "notifications": db.query(Notification).filter_by(tenant_id=tenant_id).count(),
        "audit_logs": db.query(AuditLog).filter_by(tenant_id=tenant_id).count(),
        "knowledge_docs": db.query(KnowledgeDocument).filter_by(
            tenant_id=tenant_id, is_latest=True, status="ready").count(),
    }


def reset(db: Session | None = None, tenant_id: int | None = None, confirmed: bool = False) -> dict:
    """一键重置：归档旧演示实例（关闭 + 释放固定手机号），不动任何不可变证据。

     语义:
      - 必须精确确认 tenant_id 且该租户 is_demo=True
      - 归档：status=closed、name 加归档后缀；演示账号手机号改为归档占位（释放固定号）
      - 不删除/篡改 refund_approval_events 等不可变审计数据，业务数据随归档保留
      - 知识清理：DB 文档删除、Milvus/ES 切片删除（失败明确报告 partial）、上传原始文件删除
      - pending/processing 文档置 failed，防止旧 worker 在 reset 后重新写入向量
      - reset 后运行 create 得到全新演示实例（可重复的干净基线）

     语义:
      - 只允许 active 演示租户；已 closed 的再次 reset 无修改返回 already_archived
      - 全部数据库归档操作（关租户/释放手机号/释放单号/文档置 failed/删除）在
        单个事务内完成，任一步失败整体 rollback，绝不留下半归档状态
      - 释放手机号前执行排他性检查：混合归属（同时属于非 demo 租户）账号不释放
      - 外部清理（上传文件/Milvus/ES）在数据库归档提交后单独执行，失败报告 partial
    """
    _assert_env_allowed()
    from app.core.database import SessionLocal

    if not confirmed:
        raise DemoSeedConflictError("重置前必须精确确认演示 tenant_id（--tenant-id N --yes）")
    if tenant_id is None:
        raise DemoSeedConflictError("重置必须显式提供 --tenant-id")

    own_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        from app.crud import knowledge as knowledge_crud
        from app.models.knowledge_document import KnowledgeDocument
        from app.models.tenant import Tenant

        tenant = db.query(Tenant).filter_by(id=tenant_id).first()
        if tenant is None or not tenant.is_demo:
            raise DemoSeedConflictError(
                f"tenant_id={tenant_id} 不是演示租户（is_demo 标记校验失败），拒绝归档"
            )
        # 已归档实例再次 reset → 无修改返回 already_archived；
        # 非 active（readonly 等）状态拒绝 reset。
        if tenant.status == "closed":
            return {
                "tenant_id": tenant_id,
                "already_archived": True,
                "demo_tenants_active": _count_demo_tenants(db, "active"),
                "demo_tenants_archived": _count_demo_tenants(db, "archived"),
            }
        if tenant.status != "active":
            raise DemoSeedConflictError(
                f"reset 仅允许 active 演示租户，tenant_id={tenant_id} 当前 status={tenant.status!r}"
            )

        # 1-2. 数据库归档（原子单事务）：关闭实例 + 名字加后缀 + 释放固定演示手机号
        # 与演示单号 + pending/processing 文档置 failed + 删除文档记录。
        # 任一步抛错整体 rollback，绝不留下半归档状态。
        docs: list = []
        try:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            tenant.status = "closed"
            tenant.name = f"{DEMO_TENANT_NAME}（已归档 {stamp}）"
            _release_demo_phones(db, tenant_id)
            _archive_demo_order_nos(db, tenant_id)

            docs = db.query(KnowledgeDocument).filter_by(tenant_id=tenant_id).all()
            doc_ids = [d.id for d in docs]
            for d in docs:
                if d.status in ("pending", "processing"):
                    knowledge_crud.update_status(db, d, "failed", error_message="演示租户 reset 归档清理")
            for d in docs:
                db.delete(d)
            db.commit()
        except Exception:
            db.rollback()
            raise

        # 3. 上传目录原始文件清理（{doc_id}_{filename}）—— 数据库归档成功后的外部清理，
        # 失败单独报告 partial，不影响归档结果。
        upload_cleanup = "ok"
        try:
            for doc_id in doc_ids:
                for name in os.listdir(settings.upload_storage_dir):
                    if name.startswith(f"{doc_id}_"):
                        try:
                            os.remove(os.path.join(settings.upload_storage_dir, name))
                        except OSError:
                            upload_cleanup = "partial"
        except OSError:
            upload_cleanup = "partial"

        # 4. 向量库切片清理（Milvus/ES；失败明确报告 partial，不假装全成功）
        vector_cleanup = "ok"
        from app.core.embedding_client import get_es_client, get_milvus_collection
        try:
            get_milvus_collection().delete(expr=f"tenant_id == {tenant_id}")
        except Exception as e:
            print(f"[demo-reset] Milvus 清理失败（partial）: {e}")
            vector_cleanup = "partial"
        try:
            get_es_client().delete_by_query(
                index="knowledge_chunks",
                body={"query": {"term": {"tenant_id": tenant_id}}},
                refresh=True,
            )
        except Exception as e:
            print(f"[demo-reset] ES 清理失败（partial）: {e}")
            vector_cleanup = "partial"

        return {
            "tenant_id": tenant_id,
            "archived": True,
            "vector_cleanup": vector_cleanup,
            "upload_cleanup": upload_cleanup,
            "demo_tenants_active": _count_demo_tenants(db, "active"),
            "demo_tenants_archived": _count_demo_tenants(db, "archived"),
        }
    finally:
        if own_db and db is not None:
            db.close()


def _count_demo_tenants(db: Session, which: str) -> int:
    """统计演示实例数量：active（status=active）/ archived（非 active）。"""
    from app.models.tenant import Tenant

    q = db.query(Tenant).filter(Tenant.is_demo.is_(True))
    if which == "active":
        return q.filter(Tenant.status == "active").count()
    return q.filter(Tenant.status != "active").count()


def _archive_demo_order_nos(db: Session, tenant_id: int) -> None:
    """归档时释放全局唯一演示单号：旧实例订单/工单号改为归档号。

    订单/工单号是业务标识（非不可变审计证据），改名后新演示实例可复用固定单号；
    refund_approval_events 等不可变证据原样保留。
    : 不内部 commit（由 reset 的单事务统一提交）；归档号长度受
    String(30) 列约束，超长直接报错而非截断溢出。重复 reset 由上层
    already_archived 短路保证，归档号不会无限增长。
    """
    from app.models.order import Order
    from app.models.ticket import Ticket

    for o in db.query(Order).filter_by(tenant_id=tenant_id).all():
        if o.order_no.startswith("ORD-DEMO-"):
            new_no = f"ORD-DEMO-OLD{tenant_id}-{o.order_no[len('ORD-DEMO-'):]}"
            if len(new_no) > 30:
                raise DemoSeedConflictError(
                    f"归档订单号超长（>{30} 字符）: {new_no!r}"
                )
            o.order_no = new_no
    for t in db.query(Ticket).filter_by(tenant_id=tenant_id).all():
        if t.ticket_no.startswith("TK-DEMO-"):
            new_no = f"TK-DEMO-OLD{tenant_id}-{t.ticket_no[len('TK-DEMO-'):]}"
            if len(new_no) > 30:
                raise DemoSeedConflictError(
                    f"归档工单号超长（>{30} 字符）: {new_no!r}"
                )
            t.ticket_no = new_no


def _generate_archived_phone(db: Session, *, tenant_id: int, index: int) -> str:
    """生成归档占位手机号：合法 11 位（199 演示专用段）、确定性、碰撞时递增重试。

    格式: 199 + tenant_id 后 5 位(十进制) + 身份下标 1 位 + 重试计数 2 位。
    与既有用户 phone_hash 撞车时递增重试计数，最多 100 次。
    """
    from app.core.crypto import phone_hash
    from app.models.user import User

    base = f"199{tenant_id % 100000:05d}{index}"
    for retry in range(100):
        candidate = f"{base}{retry:02d}"
        if not _CN_MOBILE_RE.match(candidate):
            break
        occupied = db.query(User).filter_by(phone_hash=phone_hash(candidate)).first()
        if occupied is None:
            return candidate
    raise DemoSeedConflictError(
        f"归档占位手机号生成失败（碰撞重试耗尽，tenant_id={tenant_id}, index={index}）"
    )


def _release_demo_phones(db: Session, tenant_id: int) -> None:
    """释放演示固定手机号：把归档实例的演示账号手机号改为归档占位号。

    : 释放前执行排他性检查--仅作用于"当前演示租户的精确角色成员且无非
    demo 混合归属"的账号；混合归属用户的手机号保持原状（不破坏普通租户登录）。
    不内部 commit（由 reset 的单事务统一提交）。
    """
    from app.core.crypto import phone_hash
    from app.models.tenant_member import TenantMember
    from app.crud import user as user_crud

    for i, phone in enumerate((DEMO_ADMIN_PHONE, DEMO_AGENT1_PHONE, DEMO_AGENT2_PHONE)):
        user = user_crud.get_by_phone(db, phone)
        if user is None:
            continue
        member = (
            db.query(TenantMember)
            .filter_by(tenant_id=tenant_id, user_id=user.id, role=_DEMO_PHONE_ROLES[phone])
            .first()
        )
        if member is None:
            continue  # 不属于本演示租户（或角色不符），不触碰
        if _has_non_demo_membership(db, user.id):
            print(
                f"[demo-reset] 账号 {phone} 存在非 demo 租户混合归属，"
                f"跳过手机号释放以保护普通租户登录"
            )
            continue
        archived_phone = _generate_archived_phone(db, tenant_id=tenant_id, index=i)
        user.phone = archived_phone  # EncryptedString 自动加密
        user.phone_hash = phone_hash(archived_phone)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo-seed", description="ZhiHub 电商演示租户 seed/reset")
    sub = parser.add_subparsers(dest="command", required=True)
    create_p = sub.add_parser("create", help="创建/补齐演示租户（幂等）")
    create_p.add_argument("--admin-password", default=None)
    create_p.add_argument("--agent-password", default=None)
    create_p.add_argument("--reset-passwords", action="store_true",
                          help="显式重置演示账号密码为本次提供的值（默认不动已有密码）")
    create_p.add_argument("--claim-legacy", action="store_true",
                          help="显式认领 d34 迁移前创建、is_demo 未标记的旧演示实例"
                               "（需通过完整演示身份集合校验；默认不认领）")
    reset_p = sub.add_parser("reset", help="归档演示租户（仅关闭演示实例，不碰不可变审计证据）")
    reset_p.add_argument("--tenant-id", type=int, required=True)
    reset_p.add_argument("--yes", action="store_true", help="确认精确 tenant_id 后执行")
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            summary = seed(
                admin_password=args.admin_password,
                agent_password=args.agent_password,
                reset_passwords=args.reset_passwords,
                claim_legacy=args.claim_legacy,
            )
            print("[demo] seed 完成:")
            for k, v in summary.items():
                print(f"  {k}: {v}")
        else:
            result = reset(tenant_id=args.tenant_id, confirmed=args.yes)
            print("[demo] reset 完成:", result)
            if result.get("vector_cleanup") != "ok" or result.get("upload_cleanup") != "ok":
                print("[demo] 警告: reset 部分清理失败（详见上方输出）")
        return 0
    except DemoSeedError as e:
        print(f"[demo] 错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
