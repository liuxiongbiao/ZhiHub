"""
backend/app/services/billing_service.py

职责: 配额计数器（Redis INCR + 套餐阈值）、80%/100% 预警、账单生成、mock 支付回调、
      用量导出 CSV、逾期账单降级标记
分工: 🔴 强模型独立完成（计费逻辑错了=多扣费/少扣费）
对应日程: Day 6

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
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """支付相关错误（验签失败/账单状态非法等）。"""


class RefundError(Exception):
    """退款调用支付宝失败。"""

# Redis 配额键前缀 -- 格式 billing:quota:{tenant_id}:{YYYY-MM}，按月分桶
QUOTA_KEY_PREFIX = "billing:quota:"
# 预警去重键前缀 -- 格式 billing:warning:{tenant_id}:{YYYY-MM}:{level}，保证每级只通知一次
WARNING_SENT_KEY = "billing:warning:"
# 009 预警横幅键前缀 -- 格式 warning_banner:{tenant_id}:{level}，/home 读取显示红色横幅（PRD 3.9）
WARNING_BANNER_KEY = "warning_banner:"
WARNING_BANNER_TTL = 60 * 60 * 24 * 40  # 40 天 TTL，覆盖整月（与去重键一致）

# 套餐配额（demo 硬编码，生产读 tenant.plan_type）
TRIAL_QUOTA = 100  # 试用套餐 100次/月
QUOTA_WARNING_80 = 0.8
QUOTA_WARNING_100 = 1.0


def _quota_key(tenant_id: int, month: str | None = None) -> str:
    """生成配额计数器键（私有 helper）：billing:quota:{tenant}:{YYYY-MM}。

    Args:
        tenant_id: 租户 ID
        month: 可选月份 "YYYY-MM"，默认当前月。generate_invoice 生成上月账单时
            必须传账期 period，否则月初会读到当前月计数（≈0），超额费恒为 0（T9）。
    """
    if month is None:
        month = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")
    return f"{QUOTA_KEY_PREFIX}{tenant_id}:{month}"


def _warning_key(tenant_id: int, level: str) -> str:
    """生成预警去重键（私有 helper）：billing:warning:{tenant}:{YYYY-MM}:{level}。"""
    month = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")
    return f"{WARNING_SENT_KEY}{tenant_id}:{month}:{level}"


def incr_usage(tenant_id: int, model: str = "deepseek/deepseek-chat") -> dict:
    """每次 Agent 有效回复后 INCR 配额计数器（010: 按模型权重折算）。

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
    import json as _json
    r = get_redis()
    key = _quota_key(tenant_id)

    # 010: 按模型权重折算（权重表从 config 读取）-- 贵模型权重高，更早耗尽配额
    try:
        from app.config import settings
        weights = _json.loads(settings.model_weights) if settings.model_weights else {}
    except Exception:
        # 权重表解析失败 -> 默认权重 1.0，降级但不阻断计费
        weights = {}
    weight = weights.get(model, 1.0)

    # 原子浮点自增 -- 并发安全，多个会话同时结算不会丢计数
    count = r.incrbyfloat(key, weight)
    r.expire(key, 60 * 60 * 24 * 40)  # 40天TTL覆盖整月

    is_overage = count >= TRIAL_QUOTA
    warning = None

    # 80% 预警（仅触发一次）-- SETNX 原子去重（T18）:
    # 原 exists+set 两步非原子，并发结算可同时触发同一级预警重复通知；
    # nx=True 保证只有首个写入者进入触发分支，键带 TTL 覆盖整月
    if count >= int(TRIAL_QUOTA * QUOTA_WARNING_80) and r.set(
        _warning_key(tenant_id, "80"), "1", ex=60 * 60 * 24 * 40, nx=True,
    ):
        # 009: 预警横幅标记（PRD 3.9），/home 读取显示红色横幅
        r.set(f"{WARNING_BANNER_KEY}{tenant_id}:80", "1", ex=WARNING_BANNER_TTL)
        warning = "80%"
        logger.info(f"Tenant {tenant_id} quota 80% warning: {count}/{TRIAL_QUOTA}")
        _notify(tenant_id, "80%", count)

    # 100% 预警（同 SETNX 原子去重）
    if count >= TRIAL_QUOTA and r.set(
        _warning_key(tenant_id, "100"), "1", ex=60 * 60 * 24 * 40, nx=True,
    ):
        # 009: 预警横幅标记（PRD 3.9），100% 覆盖 80%（/home 读最新 level）
        r.set(f"{WARNING_BANNER_KEY}{tenant_id}:100", "1", ex=WARNING_BANNER_TTL)
        warning = "100%"
        logger.info(f"Tenant {tenant_id} quota EXCEEDED: {count}/{TRIAL_QUOTA}")
        _notify(tenant_id, "100%", count)

    return {"count": count, "quota": TRIAL_QUOTA, "is_overage": is_overage, "warning": warning}


def incr_usage_for_tenant(db: Session, tenant_id: int) -> dict:
    """按租户默认模型折算计费配额（026 PRD 3.9）。

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

    return incr_usage(tenant_id, model)


def _notify(tenant_id: int, level: str, count: int) -> None:
    """触发配额预警通知（mock，失败不影响主流程）。

    设计决策: 通知失败只 log warning 不抛异常 -- 配额计数已完成，
    通知是旁路逻辑，不能因通知失败回滚计费。
    """
    try:
        from app.tasks.notification_tasks import notify_quota_warning
        notify_quota_warning(tenant_id, level, count, TRIAL_QUOTA)
    except Exception as e:
        logger.warning(f"Quota warning notification failed: {e}")


def check_quota(tenant_id: int) -> dict:
    """查询当前配额使用情况（只读）。

    Args:
        tenant_id: 租户 ID

    Returns:
        dict: count / quota / remaining(下限 0，不会返回负数)
    """
    r = get_redis()
    count = float(r.get(_quota_key(tenant_id)) or 0)
    return {"count": count, "quota": TRIAL_QUOTA, "remaining": max(0, TRIAL_QUOTA - count)}


def generate_invoice(db: Session, *, tenant_id: int, period: str) -> dict:
    """生成月度账单（mock: 基础费 99 + 超额每 10 次 10 元）。

    流程:
      1. 去重: 同 (tenant_id, period) 已有账单则直接返回（cached=True）
      2. 从 Redis 读当月配额计数
      3. 计算超额费: 超出部分每 10 次收 10 元（阶梯向下取整）
      4. 持久化账单记录，状态 unpaid

    设计决策:
      - 去重在前: 定时任务可能重复触发，避免生成多份账单
      - 超额阶梯用整除: 避免零头计费，简化对账

    Args:
        db: 数据库会话
        tenant_id: 租户 ID
        period: 账期，如 "2026-08"

    Returns:
        dict: id / period / base_fee / overage_fee / cached
    """
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select

    # 去重：同租户同周期已有账单则返回已有（避免重复生成）
    existing = db.execute(
        select(BillingInvoice).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.period == period,
        )
    ).scalar_one_or_none()
    if existing:
        return {
            "id": existing.id, "period": period,
            "base_fee": existing.base_fee, "overage_fee": existing.overage_fee, "cached": True,
        }

    r = get_redis()
    # 按账期 period 取配额 key：月初生成上月账单时读上月计数（T9 修复，
    # 原来固定当前月导致月初账单超额费恒为 0）
    count = float(r.get(_quota_key(tenant_id, month=period)) or 0)
    base_fee = 99.0
    overage_count = max(0, count - TRIAL_QUOTA)
    # 超额阶梯: 每 10 次收 10 元，整除取整避免零头
    overage_fee = (overage_count // 10) * 10.0 if overage_count > 0 else 0.0

    invoice = BillingInvoice(
        tenant_id=tenant_id, period=period, base_fee=base_fee,
        overage_fee=overage_fee, status="unpaid",
    )
    db.add(invoice)
    db.flush()  # flush 拿 id，但不 commit（由调用方控制事务）

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
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select
    from app.core.alipay_client import get_alipay_client

    client = get_alipay_client()
    if not client.verify_callback(form_data):
        logger.warning(f"Alipay callback verify failed: trade_no={form_data.get('trade_no')}")
        raise PaymentError("验签失败")

    trade_no = form_data.get("trade_no", "")
    trade_status = form_data.get("trade_status", "")

    # 按 trade_no 幂等去重 -- 同一支付流水重复回调不重复入账
    existing = db.execute(
        select(BillingInvoice).where(BillingInvoice.payment_trade_no == trade_no)
    ).scalar_one_or_none()
    if existing:
        return {"cached": True, "invoice_id": existing.id, "status": existing.status}

    # 仅 TRADE_SUCCESS / TRADE_FINISHED 视为支付成功
    if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return {"error": f"非成功状态: {trade_status}"}

    # 通过 out_trade_no（create_payment 生成的商户订单号）定位账单
    out_trade_no = form_data.get("out_trade_no", "")
    invoice = _locate_invoice_by_out_trade_no(db, out_trade_no)

    if invoice is None:
        return {"error": "未匹配到账单"}

    # T17: 原子条件更新防并发双 trade_no -- 两个并发回调都读到 unpaid 时，只有一个
    # UPDATE WHERE status='unpaid' 成功（rowcount=1），另一个 rowcount=0 拒绝且不覆盖
    # payment_trade_no。原 read-then-write（先 if status==paid 再赋值）非原子，并发双付只记一次。
    from sqlalchemy import update as _sa_update
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    res = db.execute(
        _sa_update(BillingInvoice)
        .where(BillingInvoice.id == invoice.id, BillingInvoice.status == "unpaid")
        .values(status="paid", payment_trade_no=trade_no, paid_at=now)
    )
    if res.rowcount == 0:
        return {"error": f"账单已支付或状态已变: {invoice.id}"}
    # PRD 2.1 审计日志: 支付回调成功落库（同事务）
    log_audit(
        db, tenant_id=invoice.tenant_id, user_id=None, action="payment",
        target_type="billing_invoice", target_id=invoice.id,
        detail=f"trade_no={trade_no}",
    )
    db.commit()

    # 支付成功恢复降级 -- 清除租户降级标记（PRD 3.10 要求）
    _clear_degradation(invoice.tenant_id)

    logger.info(f"Payment callback success: invoice={invoice.id}, trade_no={trade_no}")
    return {"invoice_id": invoice.id, "status": "paid", "cached": False}


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


# 010 US4: 用量导出 + 逾期降级
def export_usage(db: Session, tenant_id: int) -> str:
    """导出租户用量 CSV（010 US4）。

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


def check_overdue(db: Session) -> dict:
    """检查逾期账单 -> 降级标记（010 US4）。

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
        # 降级标记 24h TTL -- 每日定时任务刷新；租户补缴后标记自然过期
        r.set(f"billing:degraded:{inv.tenant_id}", "1", ex=86400)
        logger.info(f"Tenant {inv.tenant_id} marked degraded (overdue invoice {inv.id})")
    return {"degraded": len(overdue)}


def is_degraded(tenant_id: int) -> bool:
    """检查租户是否被降级（010 US4）。

    业务层在处理请求前调用本函数，降级则拒绝或限制功能。

    Args:
        tenant_id: 租户 ID

    Returns:
        bool: True 表示已降级（逾期未付款）
    """
    return get_redis().exists(f"billing:degraded:{tenant_id}") == 1
