"""
backend/app/services/billing_service.py

职责: 配额计数器（Redis INCR + 套餐阈值）、80%/100% 预警、账单生成、mock 支付回调、
      用量导出 CSV、逾期账单降级标记

核心设计:
  以 Redis INCRBYFLOAT 为配额计数器，按"模型权重"折算用量（贵模型权重高），
  每次有效回复后累加。达到 80%/100% 阈值时各触发一次预警（用独立键去重保证只通知一次）。
  月度账单 = 基础费 99 + 超额阶梯计费。逾期未付的账单会在 Redis 打降级标记，
  业务层据此限制租户功能。

关键技术点:
  - Redis INCRBYFLOAT: 浮点原子自增，支持按权重折算（0.5 次调用也算得清）
  - 预警去重键: warning:{tenant}:{month}:{level} 设置后不再重复通知，TTL 覆盖整月
  - 账单去重: 同 (tenant_id, period) 只生成一次，幂等保证
  - 降级标记: billing:degraded:{tenant_id} 设 24h TTL，定时任务每日刷新

依赖关系:
  - 上游: app/api 层调用 incr_usage/check_quota/generate_invoice；定时任务调用 check_overdue
  - 下游: app.core.redis（计数器）、app.models.billing_invoice（账单持久化）、
          app.tasks.notification_tasks（预警通知）、app.config.settings（权重/阈值）
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from redis.exceptions import WatchError
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """支付相关错误（验签失败/账单状态非法等）。"""


class RefundError(Exception):
    """退款调用支付宝失败。"""

# Redis 配额键前缀 —— 格式 billing:quota:{tenant_id}:{YYYY-MM}，按月分桶
QUOTA_KEY_PREFIX = "billing:quota:"
# 预警去重键前缀 —— 格式 billing:warning:{tenant_id}:{YYYY-MM}:{level}，保证每级只通知一次
WARNING_SENT_KEY = "billing:warning:"
# 预警横幅键前缀 —— 格式 warning_banner:{tenant_id}:{level}，/home 读取显示红色横幅（PRD §3.9）
WARNING_BANNER_KEY = "warning_banner:"
WARNING_BANNER_TTL = 60 * 60 * 24 * 40  # 40 天 TTL，覆盖整月（与去重键一致）

# 套餐配额（demo 硬编码，生产读 tenant.plan_type）
TRIAL_QUOTA = 100  # 试用套餐 100次/月
PLAN_QUOTA = {"trial": 100, "basic": 2000, "pro": 10000, "enterprise": 100000}

QUOTA_WARNING_80 = 0.8
QUOTA_WARNING_100 = 1.0


def _quota_key(tenant_id: int, month: str | None = None) -> str:
    """生成配额计数器键（私有 helper）：billing:quota:{tenant}:{YYYY-MM}。

    Args:
        tenant_id: 租户 ID
        month: 可选月份 "YYYY-MM"，默认当前月。generate_invoice 生成上月账单时
            必须传账期 period，否则月初会读到当前月计数（≈0），超额费恒为 0。
    """
    if month is None:
        month = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")
    return f"{QUOTA_KEY_PREFIX}{tenant_id}:{month}"


def _warning_key(tenant_id: int, level: str) -> str:
    """生成预警去重键（私有 helper）：billing:warning:{tenant}:{YYYY-MM}:{level}。"""
    month = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")
    return f"{WARNING_SENT_KEY}{tenant_id}:{month}:{level}"


def usage_amount(model: str, tokens: int = 0) -> float:
    """按规格 015 的模型权重与 Token 基数计算一次会话的等效用量。"""
    import json as _json
    from app.config import settings

    try:
        weights = _json.loads(settings.model_weights) if settings.model_weights else {}
    except Exception:
        weights = {}
    weight = float(weights.get(model, 1.0))
    if tokens and tokens > 0:
        try:
            tokens_per_conv = float(settings.billing_tokens_per_conversation) or 2500.0
        except Exception:
            tokens_per_conv = 2500.0
        return (tokens / tokens_per_conv) * weight
    return weight


def _increment_usage(
    redis_client,
    *,
    quota_key: str,
    amount: float,
    event_key: str | None,
) -> tuple[float, bool]:
    """原子写入计费增量；event_key 存在时同时完成 Redis 侧幂等判重。"""
    if event_key is None:
        count = redis_client.incrbyfloat(quota_key, amount)
        redis_client.expire(quota_key, 60 * 60 * 24 * 40)
        return float(count), False

    marker_key = f"billing:event:{event_key}"
    while True:
        with redis_client.pipeline(transaction=True) as pipeline:
            try:
                pipeline.watch(marker_key)
                if pipeline.exists(marker_key):
                    pipeline.unwatch()
                    return float(redis_client.get(quota_key) or 0), True
                pipeline.multi()
                # 计费事件在数据库中永久唯一，Redis 判重键同样不设 TTL，
                # 避免极晚重放（例如长期故障恢复）再次增加配额。
                pipeline.set(marker_key, "1")
                pipeline.incrbyfloat(quota_key, amount)
                pipeline.expire(quota_key, 60 * 60 * 24 * 40)
                result = pipeline.execute()
                return float(result[1]), False
            except WatchError:
                continue


def _arm_quota_warning(redis_client, tenant_id: int, level: str, count: float, quota: int, message: str) -> bool:
    """同一级别只触发一次预警。并发下只有第一个写入者成功。"""
    if not redis_client.set(
        _warning_key(tenant_id, level), "1", ex=60 * 60 * 24 * 40, nx=True,
    ):
        return False
    redis_client.set(f"{WARNING_BANNER_KEY}{tenant_id}:{level}", "1", ex=WARNING_BANNER_TTL)
    logger.info(message)
    _notify(tenant_id, f"{level}%", count, quota)
    return True


def incr_usage(
    tenant_id: int, model: str = "deepseek/deepseek-chat", quota: int | None = None,
    tokens: int = 0, *, event_key: str | None = None, equivalent: float | None = None,
) -> dict:
    """每次 Agent 有效回复后 INCR 配额计数器（规格 010: 按模型权重折算）。

    仅在会话 RESOLVED 时调用，RESOLVED 前的中断不计入。

    流程:
      1. 从 config 读取模型权重表 JSON，取当前模型的权重（默认 1.0）
      2. Redis INCRBYFLOAT 按权重累加配额计数器，设 40 天 TTL（覆盖整月+跨月缓冲）
      3. 达到 80% 阈值且未通知过 -> 触发预警并标记去重键
      4. 达到 100% 阈值且未通知过 -> 触发超额预警并标记去重键

    设计决策:
      - 用 INCRBYFLOAT 而非 INCR: 权重可能是小数（如 0.5），整数自增无法表达
      - 预警去重键独立于计数键: 防止 Redis 重启计数归零后又重复发预警
      - TTL 40 天: 一个月最多 31 天，留余量确保整月窗口内键不过期

    Args:
        tenant_id: 租户 ID
        model: 模型标识，用于查权重表折算用量

    Returns:
        dict: count / quota / is_overage / warning(80%/100%/None)
    """
    r = get_redis()
    key = _quota_key(tenant_id)

    # 原子浮点自增；事件消费同时在 Redis 事务中写幂等键，重放不会重复增量。
    amount = equivalent if equivalent is not None else usage_amount(model, tokens)
    count, duplicate_event = _increment_usage(
        r, quota_key=key, amount=float(amount), event_key=event_key,
    )

    q = quota if quota is not None else TRIAL_QUOTA
    is_overage = count >= q
    warning = None

    if count >= int(q * QUOTA_WARNING_80) and _arm_quota_warning(
        r, tenant_id, "80", count, q, f"Tenant {tenant_id} quota 80% warning: {count}/{q}",
    ):
        warning = "80%"

    if count >= q and _arm_quota_warning(
        r, tenant_id, "100", count, q, f"Tenant {tenant_id} quota EXCEEDED: {count}/{q}",
    ):
        warning = "100%"
        from app.services.analytics_service import track_event
        track_event(
            "quota_exceeded",
            tenant_id=tenant_id,
            properties={"current_usage": count, "quota": q},
        )

    return {
        "count": count,
        "quota": q,
        "is_overage": is_overage,
        "warning": warning,
        "duplicate_event": duplicate_event,
    }


def incr_usage_for_tenant(db: Session, tenant_id: int, tokens: int = 0) -> dict:
    """按租户默认模型折算计费配额（PRD §3.9）。

    读取 tenant.default_model，调 incr_usage 按该模型权重折算。
    租户不存在或 default_model 为空 -> 降级用默认模型 deepseek/deepseek-chat。

    设计决策:
      - 与 incr_usage 解耦: incr_usage 只管 Redis 计数 + 权重折算（无 DB 依赖），
        本函数负责读租户配置选模型，职责分离便于单测。
      - 降级策略: 租户查询失败不阻断计费，用默认权重 1.0 兜底。

    Args:
        db: 数据库会话（读 tenant.default_model）
        tenant_id: 租户 ID

    Returns:
        dict: 同 incr_usage（count / quota / is_overage / warning）
    """
    from app.crud import tenant as tenant_crud

    model = "deepseek/deepseek-chat"
    try:
        tenant = tenant_crud.get_by_id(db, tenant_id)
        if tenant and tenant.default_model:
            model = tenant.default_model
    except Exception as e:
        logger.warning(f"Read tenant.default_model failed, fallback to {model}: {e}")

    # 必须按租户套餐配额判定超额（此前漏传 quota，全部按 trial=100 计数）
    quota = get_tenant_quota(db, tenant_id)
    return incr_usage(tenant_id, model, quota=quota, tokens=tokens)


def create_session_billing_event(db: Session, session) -> object:
    """把已原子领取的会话结算为不可重复的数据库计费事件。"""
    from app.crud import billing as billing_crud
    from app.crud import tenant as tenant_crud

    model = "deepseek/deepseek-chat"
    tenant = tenant_crud.get_by_id(db, session.tenant_id)
    if tenant is not None and tenant.default_model:
        model = tenant.default_model
    tokens = session_tokens(db, session.id)
    amount = Decimal(str(usage_amount(model, tokens)))
    return billing_crud.create_event(
        db,
        session_id=session.id,
        tenant_id=session.tenant_id,
        model=model,
        tokens=tokens,
        usage_amount=amount,
    )


def consume_pending_billing_events(db: Session, limit: int = 100) -> dict:
    """消费待处理计费事件；Redis 失败保留 pending，后续扫描可安全重放。"""
    from app.crud import billing as billing_crud

    processed = 0
    failed = 0
    for event in billing_crud.list_pending_events(db, limit=limit):
        event.attempts += 1
        try:
            quota = get_tenant_quota(db, event.tenant_id)
            incr_usage(
                event.tenant_id,
                event.model,
                quota=quota,
                tokens=event.tokens,
                event_key=event.event_key,
                equivalent=float(event.usage_amount),
            )
        except Exception as exc:
            event.last_error = str(exc)
            db.commit()
            failed += 1
            logger.warning(
                f"Billing event delivery failed (event={event.id}, session={event.session_id}): {exc}"
            )
            continue

        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        event.last_error = None
        # Redis 已以 event_key 去重；若此 commit 失败，下次重放只补 DB 投影。
        db.commit()
        processed += 1
    return {"processed": processed, "failed": failed}


def session_tokens(db: Session, session_id: int) -> int:
    """统计会话累计 Token 消耗（从 messages.metadata.tokens 聚合，供 RESOLVED 计费与导出）。"""
    from app.models.message import Message
    from sqlalchemy import select

    messages = db.execute(
        select(Message).where(Message.session_id == session_id)
    ).scalars().all()
    total = 0
    for m in messages:
        md = m.msg_metadata or {}
        try:
            total += int(float(md.get("tokens", 0) or 0))
        except (TypeError, ValueError):
            continue
    return total


def _notify(tenant_id: int, level: str, count: int, quota: int) -> None:
    """触发配额预警通知（写站内信，失败不影响主流程）。

    设计决策: 通知失败只 log warning 不抛异常 -- 配额计数已完成，
    通知是旁路逻辑，不能因通知失败回滚计费。
    """
    try:
        from app.tasks.notification_tasks import notify_quota_warning
        notify_quota_warning(tenant_id, level, count, quota)
    except Exception as e:
        logger.warning(f"Quota warning notification failed: {e}")


def check_quota(tenant_id: int, quota: int | None = None) -> dict:
    """查询当前配额使用情况（只读）。

    Args:
        tenant_id: 租户 ID

    Returns:
        dict: count / quota / remaining(下限 0，不会返回负数)
    """
    r = get_redis()
    count = float(r.get(_quota_key(tenant_id)) or 0)
    q = quota if quota is not None else TRIAL_QUOTA
    return {"count": count, "quota": q, "remaining": max(0, q - count)}


def _cached_invoice(db: Session, tenant_id: int, period: str) -> dict | None:
    """同一租户同一账期已有账单时返回原账单，避免重复生成。"""
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select

    existing = db.execute(
        select(BillingInvoice).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.period == period,
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    return {
        "id": existing.id,
        "period": period,
        "base_fee": existing.base_fee,
        "overage_fee": existing.overage_fee,
        "cached": True,
    }


def _period_conversation_count(db: Session, tenant_id: int, period: str) -> float:
    """优先汇总已落库的用量。该账期没有记录时，才回退到 Redis 计数。"""
    from app.models.usage_record import UsageRecord
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    total = db.execute(
        select(sa_func.coalesce(sa_func.sum(UsageRecord.conversation_count), 0)).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.date.like(f"{period}-%"),
        )
    ).scalar() or 0
    count = float(total)
    if count == 0:
        count = float(get_redis().get(_quota_key(tenant_id, month=period)) or 0)
        logger.warning(
            f"Invoice {tenant_id}/{period}: usage_records empty, fallback to Redis counter={count}"
        )
    return count


def generate_invoice(db: Session, *, tenant_id: int, period: str) -> dict:
    """生成月度账单（公式见 docs/specs/015-billing-accuracy/spec.md）。

    流程:
      1. 去重: 同 (tenant_id, period) 已有账单则直接返回（cached=True）
      2. 从 Redis 读当月配额计数
      3. 按套餐计算: base_fee = PLAN_BASE_FEES[plan_type]，
         overage_fee = max(0, count - quota) * OVERAGE_PRICE
      4. 持久化账单记录，状态 unpaid

    业务公式示例（basic，月计数 2100）:
      base_fee = 999；overage_cnt = 100；overage_fee = 100 * 0.5 = 50.00；total = 1049.00

    Args:
        db: 数据库会话
        tenant_id: 租户 ID
        period: 账期，如 "2026-08"

    Returns:
        dict: id / period / base_fee / overage_fee / cached
    """
    cached = _cached_invoice(db, tenant_id, period)
    if cached is not None:
        return cached

    from app.config import settings
    from app.crud import tenant as tenant_crud
    from app.models.billing_invoice import BillingInvoice

    tenant = tenant_crud.get_by_id(db, tenant_id)
    plan_type = tenant.plan_type if tenant else "trial"
    base_fee = _plan_base_fee(plan_type)
    quota = PLAN_QUOTA.get(plan_type, TRIAL_QUOTA)
    count = _period_conversation_count(db, tenant_id, period)
    overage_count = max(0, count - quota)
    overage_fee = round(overage_count * settings.billing_overage_price, 2)

    invoice = BillingInvoice(
        tenant_id=tenant_id, period=period, base_fee=base_fee,
        overage_fee=overage_fee, status="unpaid",
    )
    db.add(invoice)
    db.flush()

    logger.info(f"Invoice generated: tenant={tenant_id}, total={base_fee + overage_fee:.2f}")
    return {"id": invoice.id, "period": period, "base_fee": base_fee, "overage_fee": overage_fee}


def create_payment(db: Session, *, invoice_id: int, tenant_id: int) -> dict:
    """生成支付宝订单 + 支付链接（PRD 3.10）。

    流程:
      1. 加载账单，校验租户归属 + 状态（仅 unpaid 可发起支付）
      2. 生成 trade_no（商户订单号，幂等键）
      3. 调 alipay_client.create_payment 拿支付跳转 URL

    设计决策:
      - 不在创建阶段回填 payment_trade_no：未支付完成前 trade_no 不绑定账单，
        避免用户放弃支付后该 trade_no 占位导致后续无法重新发起。
        trade_no 在回调验签通过后才写入账单（幂等去重依据）。
      - 租户校验: 跨租户不可为他人账单发起支付。

    Args:
        db: 数据库会话
        invoice_id: 账单 ID
        tenant_id: 租户 ID（租户隔离）

    Returns:
        dict: invoice_id / trade_no / pay_url / amount
    """
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select
    from app.core.alipay_client import get_alipay_client

    invoice = db.execute(
        select(BillingInvoice).where(BillingInvoice.id == invoice_id)
    ).scalar_one_or_none()
    if invoice is None:
        raise PaymentError("账单不存在")
    if invoice.tenant_id != tenant_id:
        raise PaymentError("无权操作该账单")
    if invoice.status == "paid":
        raise PaymentError("账单已支付，无需重复支付")

    amount = float(invoice.base_fee) + float(invoice.overage_fee)
    # 商户订单号: ALI-{invoice_id}-{uuid8}，全局唯一，作幂等键
    trade_no = f"ALI-{invoice.id}-{uuid.uuid4().hex[:8]}"
    subject = f"ZhiHub账单 {invoice.period}"

    client = get_alipay_client()
    pay_url = client.create_payment(trade_no, amount, subject)

    logger.info(f"Payment created: invoice={invoice.id}, trade_no={trade_no}, mock={client.is_mock}")
    return {
        "invoice_id": invoice.id,
        "trade_no": trade_no,
        "pay_url": pay_url,
        "amount": amount,
    }


def _invoice_for_trade(db: Session, trade_no: str):
    """按支付流水查找已经入账的账单。"""
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select

    return db.execute(
        select(BillingInvoice).where(BillingInvoice.payment_trade_no == trade_no)
    ).scalar_one_or_none()


def _claim_unpaid_invoice(db: Session, invoice, trade_no: str) -> bool:
    """只有仍未支付的账单能被这一笔流水抢占。"""
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import update as sa_update

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = db.execute(
        sa_update(BillingInvoice)
        .where(BillingInvoice.id == invoice.id, BillingInvoice.status == "unpaid")
        .values(status="paid", payment_trade_no=trade_no, paid_at=now)
    )
    return result.rowcount != 0


def _apply_verified_payment(db: Session, form_data: dict, trade_no: str) -> dict:
    """验签通过后按流水去重、抢占未支付账单，并清除降级标记。"""
    trade_status = form_data.get("trade_status", "")
    existing = _invoice_for_trade(db, trade_no)
    if existing:
        return {"cached": True, "invoice_id": existing.id, "status": existing.status}
    if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return {"error": f"非成功状态: {trade_status}"}

    invoice = _locate_invoice_by_out_trade_no(db, form_data.get("out_trade_no", ""))
    if invoice is None:
        return {"error": "未匹配到账单"}
    if not _claim_unpaid_invoice(db, invoice, trade_no):
        return {"error": f"账单已支付或状态已变: {invoice.id}"}
    log_audit(
        db, tenant_id=invoice.tenant_id, user_id=None, action="payment",
        target_type="billing_invoice", target_id=invoice.id,
        detail=f"trade_no={trade_no}",
    )
    db.commit()

    from app.services.analytics_service import track_event
    track_event(
        "invoice_paid",
        tenant_id=invoice.tenant_id,
        properties={
            "invoice_id": invoice.id,
            "amount": float(invoice.base_fee) + float(invoice.overage_fee),
            "payment_trade_no": trade_no,
        },
    )
    _clear_degradation(invoice.tenant_id)
    logger.info(f"Payment callback success: invoice={invoice.id}, trade_no={trade_no}")
    return {"invoice_id": invoice.id, "status": "paid", "cached": False}


def handle_payment_callback(db: Session, *, form_data: dict, tenant_id: int | None = None) -> dict:
    """支付宝异步回调处理：验签 -> 幂等 -> 更新账单（PRD 3.10）。

    流程:
      1. 调 alipay_client.verify_callback 验签，失败抛 PaymentError
      2. 按 trade_no 查账单，已存在则返回 cached（幂等去重）
      3. 通过 out_trade_no 定位账单（支付宝回调回传商户订单号），标记 paid + 回填 trade_no + paid_at
      4. 支付成功清除租户降级标记（恢复服务）

    设计决策:
      - 验签在最前: 资金安全核心，不验签或弱验签=可被伪造回调伪造支付
      - 幂等以 trade_no 为键: 同一支付流水重复回调只入账一次
      - mock 模式 verify_callback 直接 True（开发/测试）

    Args:
        db: 数据库会话
        form_data: 支付宝 POST 表单（含 trade_no/out_trade_no/trade_status/sign）
        tenant_id: 租户 ID（可选，回调验签通过后从 out_trade_no 反查账单归属）

    Returns:
        dict: cached / invoice_id / status
    """
    from app.core.alipay_client import get_alipay_client

    client = get_alipay_client()
    if not client.verify_callback(form_data):
        logger.warning(f"Alipay callback verify failed: trade_no={form_data.get('trade_no')}")
        raise PaymentError("验签失败")

    return _apply_verified_payment(db, form_data, form_data.get("trade_no", ""))


def _locate_invoice_by_out_trade_no(db: Session, out_trade_no: str):
    """从商户订单号 ALI-{invoice_id}-{uuid8} 反查账单（私有 helper）。"""
    from app.models.billing_invoice import BillingInvoice

    if not out_trade_no or not out_trade_no.startswith("ALI-"):
        return None
    parts = out_trade_no.split("-", 2)
    if len(parts) < 2:
        return None
    try:
        invoice_id = int(parts[1])
    except ValueError:
        return None
    return db.get(BillingInvoice, invoice_id)


def _clear_degradation(tenant_id: int) -> None:
    """支付成功后清除租户降级标记（恢复服务）。"""
    try:
        r = get_redis()
        r.delete(f"billing:degraded:{tenant_id}")
        logger.info(f"Degradation cleared for tenant {tenant_id} after payment")
    except Exception as e:
        logger.warning(f"Clear degradation failed (non-blocking): {e}")


# 规格 010 US4: 用量导出 + 逾期降级
def export_usage(db: Session, tenant_id: int) -> str:
    """导出租户用量 CSV（规格 010 US4）。

    流程:
      1. 查询租户最近 1000 条用量记录（按创建时间倒序）
      2. 写入 CSV 字符串返回

    Args:
        db: 数据库会话
        tenant_id: 租户 ID

    Returns:
        str: CSV 文本
    """
    import io, csv
    from app.models.usage_record import UsageRecord
    from sqlalchemy import select

    records = db.execute(
        select(UsageRecord).where(UsageRecord.tenant_id == tenant_id)
        .order_by(UsageRecord.created_at.desc()).limit(1000)
    ).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["tenant_id", "date", "conversation_count", "is_overage"])
    for r in records:
        writer.writerow([r.tenant_id, r.date, r.conversation_count, r.is_overage])
    return output.getvalue()


def _session_token_totals(db: Session, session_ids: list[int]) -> dict[int, int]:
    """按会话汇总消息元数据里的 token。无法解析的值跳过。"""
    from app.models.message import Message
    from sqlalchemy import select

    totals: dict[int, int] = {}
    if not session_ids:
        return totals
    messages = db.execute(
        select(Message).where(Message.session_id.in_(session_ids))
    ).scalars().all()
    for message in messages:
        metadata = message.msg_metadata or {}
        try:
            totals[message.session_id] = totals.get(message.session_id, 0) + int(
                float(metadata.get("tokens", 0) or 0)
            )
        except (TypeError, ValueError):
            continue
    return totals


def export_usage_detail(db: Session, tenant_id: int) -> str:
    """导出用量与 Token 消耗明细（PRD 3.9）：按会话一行，
    字段含会话ID、发生时间、意图类型、调用模型、Token 数、是否计入超额。"""
    import csv
    import io

    from app.models.session import Session as SessionModel
    from app.crud import tenant as tenant_crud
    from sqlalchemy import select

    tenant = tenant_crud.get_by_id(db, tenant_id)
    model = tenant.default_model if tenant and tenant.default_model else "deepseek/deepseek-chat"
    quota = get_tenant_quota(db, tenant_id)

    sessions = db.execute(
        select(SessionModel).where(SessionModel.tenant_id == tenant_id)
        .order_by(SessionModel.created_at.asc())
    ).scalars().all()
    tokens_by_session = _session_token_totals(db, [session.id for session in sessions])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "session_id", "occurred_at", "intent_type", "model", "tokens", "is_overage",
    ])
    cumulative = 0.0
    for s in sessions:
        tokens = tokens_by_session.get(s.id, 0)
        cumulative += max(tokens / 2500.0, 0.0)
        writer.writerow([
            s.id, s.created_at, s.intent or "", model, tokens,
            "true" if cumulative >= quota else "false",
        ])
    return output.getvalue()


def check_overdue(db: Session) -> dict:
    """检查逾期账单 -> 降级标记（规格 010 US4）。

    流程:
      1. 计算逾期时间阈值 = now - invoice_overdue_days
      2. 查所有 created_at 早于阈值且仍 unpaid 的账单
      3. 对每个逾期租户在 Redis 设降级标记（24h TTL）

    设计决策:
      - 降级标记用 24h TTL 而非永久: 定时任务每日刷新，若租户补缴则标记自然过期恢复
      - 按 created_at 而非 invoice.period 判定逾期: 简化逻辑，避免时区/账期换算

    Args:
        db: 数据库会话

    Returns:
        dict: degraded(本次标记降级的账单数)
    """
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select
    from datetime import timedelta
    from app.config import settings

    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=settings.invoice_overdue_days)
    overdue = db.execute(
        select(BillingInvoice).where(
            BillingInvoice.status == "unpaid",
            BillingInvoice.created_at < threshold,
        )
    ).scalars().all()

    r = get_redis()
    for inv in overdue:
        # 试用租户默认不收费，历史遗留账单也不触发降级
        from app.crud import tenant as tenant_crud
        tenant = tenant_crud.get_by_id(db, inv.tenant_id)
        if tenant is not None and tenant.plan_type == "trial" and not settings.billing_charge_trial:
            continue
        # 降级标记 24h TTL —— 每日定时任务刷新；租户补缴后标记自然过期
        r.set(f"billing:degraded:{inv.tenant_id}", "1", ex=86400)
        logger.info(f"Tenant {inv.tenant_id} marked degraded (overdue invoice {inv.id})")
    return {"degraded": len(overdue)}


def is_degraded(tenant_id: int) -> bool:
    """检查租户是否被降级（规格 010 US4）。

    业务层在处理请求前调用本函数，降级则拒绝或限制功能。

    Args:
        tenant_id: 租户 ID

    Returns:
        bool: True 表示已降级（逾期未付款）
    """
    return get_redis().exists(f"billing:degraded:{tenant_id}") == 1

def get_tenant_quota(db: Session, tenant_id: int) -> int:
    """按套餐返回租户月对话配额（PRD 3.9 套餐矩阵，fresh-eyes ）。"""
    try:
        from app.crud import tenant as tenant_crud
        t = tenant_crud.get_by_id(db, tenant_id)
        if t is not None:
            if t.quota_conversations_per_month > 0:
                return t.quota_conversations_per_month
            return PLAN_QUOTA.get(t.plan_type, TRIAL_QUOTA)
    except Exception:
        pass
    return TRIAL_QUOTA


def _plan_base_fee(plan_type: str) -> float:
    """读取套餐月基础费（规格 015：从配置 JSON 读取，解析失败回退 0 并告警）。"""
    import json as _json
    try:
        from app.config import settings
        fees = _json.loads(settings.billing_plan_base_fees) if settings.billing_plan_base_fees else {}
        return float(fees.get(plan_type, 0.0))
    except Exception as e:
        logger.warning(f"billing_plan_base_fees parse failed, fallback 0: {e}")
        return 0.0
