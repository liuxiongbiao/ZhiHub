"""运营埋点与看板聚合服务。

业务事件写入 MongoDB，采用失败放行策略，绝不反向阻断会话、支付或退款主链路；
看板按租户聚合，平台全局视图只能由额外的平台运营令牌访问。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.utils.desensitize import mask_pii

logger = logging.getLogger(__name__)

EVENT_TTL_SECONDS = 365 * 24 * 3600
MAX_EVENTS_PER_QUERY = 100_000
ALLOWED_EVENTS = {
    "session_created",
    "retrieval_executed",
    "mcp_tool_called",
    "human_takeover",
    "order_state_changed",
    "refund_approved",
    "refund_rejected",
    "quota_exceeded",
    "invoice_paid",
    "security_block",
    "answer_feedback",
    "index_rebuild_triggered",
    "degradation_mode_entered",
    "degradation_mode_exited",
    "message_left",
    # 看板自动解决率需要一个明确终态事件，避免用“无人工事件”反向猜测。
    "session_resolved",
}

_client = None
_collection = None
_indexes_ready = False
_client_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_collection():
    """延迟创建短超时 Mongo 连接，便于业务进程在 Mongo 故障时快速放行。"""
    global _client, _collection
    if _collection is not None:
        return _collection
    with _client_lock:
        if _collection is None:
            from pymongo import MongoClient

            _client = MongoClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=500,
                connectTimeoutMS=500,
                socketTimeoutMS=1000,
            )
            _collection = _client["zhihub"]["analytics_events"]
    return _collection


def _ensure_indexes(collection) -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    with _client_lock:
        if _indexes_ready:
            return
        collection.create_index(
            [("tenant_id", 1), ("event_name", 1), ("occurred_at", -1)],
            name="tenant_event_time",
        )
        collection.create_index(
            "occurred_at",
            expireAfterSeconds=EVENT_TTL_SECONDS,
            name="analytics_event_ttl",
        )
        _indexes_ready = True


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """限制事件属性体积并脱敏所有字符串，防止原始手机号等 PII 落库。"""
    if depth > 3:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return mask_pii(value[:1000])
    if isinstance(value, dict):
        return {
            str(key)[:80]: _sanitize(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:50]]
    return mask_pii(str(value)[:1000])


def track_event(
    event_name: str,
    *,
    tenant_id: int | None,
    properties: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    """写入一条白名单业务事件；存储不可用时返回 False，不抛出业务异常。"""
    if event_name not in ALLOWED_EVENTS:
        logger.warning("Rejected unknown analytics event: %s", event_name)
        return False
    if tenant_id is None:
        logger.warning("Skipped analytics event without tenant: %s", event_name)
        return False
    try:
        collection = _get_collection()
        _ensure_indexes(collection)
        collection.insert_one(
            {
                "event_name": event_name,
                "tenant_id": int(tenant_id),
                "properties": _sanitize(properties or {}),
                "occurred_at": occurred_at or _utcnow(),
            }
        )
        return True
    except Exception as exc:  # 埋点不得影响主链路
        logger.warning("Analytics event write failed (%s): %s", event_name, exc)
        return False


def _load_events(tenant_id: int | None, since: datetime) -> tuple[list[dict], bool]:
    query: dict[str, Any] = {"occurred_at": {"$gte": since}}
    if tenant_id is not None:
        query["tenant_id"] = tenant_id
    try:
        cursor = (
            _get_collection()
            .find(query, {"_id": 0})
            .sort("occurred_at", -1)
            .limit(MAX_EVENTS_PER_QUERY)
        )
        return list(cursor), True
    except Exception as exc:
        logger.warning("Analytics event query failed: %s", exc)
        return [], False


def _day_labels(since: datetime, now: datetime) -> list[str]:
    days = (now.date() - since.date()).days
    return [(since.date() + timedelta(days=offset)).isoformat() for offset in range(days + 1)]


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _event_day(event: dict) -> str:
    occurred = event.get("occurred_at")
    return occurred.date().isoformat() if isinstance(occurred, datetime) else ""


def _retrieval_comparison(events: list[dict]) -> list[dict]:
    retrievals = [e.get("properties", {}) for e in events if e.get("event_name") == "retrieval_executed"]
    definitions = (
        ("vector", "纯向量", "vector_top_score"),
        ("hybrid", "混合检索", "hybrid_top_score"),
        ("hyde", "HyDE 兜底", "hybrid_top_score"),
    )
    rows = []
    for mode, label, score_key in definitions:
        selected = retrievals if mode != "hyde" else [p for p in retrievals if p.get("hyde_fallback")]
        scores = [_as_float(p.get(score_key)) for p in selected if p.get(score_key) is not None]
        latencies = [_as_float(p.get("latency")) for p in selected]
        rows.append(
            {
                "mode": mode,
                "label": label,
                "executions": len(selected),
                "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            }
        )
    return rows


def _resolution_trend(events: list[dict], labels: list[str]) -> list[dict]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"auto": 0, "human": 0})
    for event in events:
        if event.get("event_name") != "session_resolved":
            continue
        day = _event_day(event)
        mode = event.get("properties", {}).get("resolution_mode")
        grouped[day]["human" if mode == "human" else "auto"] += 1
    rows = []
    for day in labels:
        auto, human = grouped[day]["auto"], grouped[day]["human"]
        total = auto + human
        rows.append(
            {
                "date": day,
                "auto": auto,
                "human": human,
                "auto_rate": round(auto / total * 100, 1) if total else 0,
            }
        )
    return rows


def _approval_latency(events: list[dict]) -> tuple[list[dict], float]:
    values = [
        _as_float(event.get("properties", {}).get("latency_seconds"))
        for event in events
        if event.get("event_name") in {"refund_approved", "refund_rejected"}
    ]
    buckets = [
        ("1 小时内", 0, 3600),
        ("1–4 小时", 3600, 4 * 3600),
        ("4–24 小时", 4 * 3600, 24 * 3600),
        ("24 小时以上", 24 * 3600, float("inf")),
    ]
    distribution = [
        {"label": label, "count": sum(1 for value in values if start <= value < end)}
        for label, start, end in buckets
    ]
    return distribution, round(sum(values) / len(values), 1) if values else 0


def _plan_distribution(db: DbSession, tenant_id: int | None) -> list[dict]:
    from app.models.tenant import Tenant

    stmt = select(Tenant.plan_type, func.count(Tenant.id)).where(Tenant.status != "closed")
    if tenant_id is not None:
        stmt = stmt.where(Tenant.id == tenant_id)
    rows = db.execute(stmt.group_by(Tenant.plan_type)).all()
    return [{"plan": plan, "count": count} for plan, count in rows]


def _revenue_trend(db: DbSession, tenant_id: int | None, since: datetime) -> list[dict]:
    from app.models.billing_invoice import BillingInvoice

    stmt = select(
        BillingInvoice.period,
        func.sum(BillingInvoice.base_fee + BillingInvoice.overage_fee),
    ).where(BillingInvoice.status == "paid", BillingInvoice.paid_at >= since)
    if tenant_id is not None:
        stmt = stmt.where(BillingInvoice.tenant_id == tenant_id)
    rows = db.execute(stmt.group_by(BillingInvoice.period).order_by(BillingInvoice.period)).all()
    return [{"period": period, "revenue": round(_as_float(amount), 2)} for period, amount in rows]


def _average_csat(db: DbSession, tenant_id: int | None, since: datetime) -> float:
    from app.models.csat import CsatEvaluation
    from app.models.session import Session

    stmt = (
        select(func.avg(CsatEvaluation.score))
        .join(Session, Session.id == CsatEvaluation.session_id)
        .where(CsatEvaluation.created_at >= since, CsatEvaluation.score.is_not(None))
    )
    if tenant_id is not None:
        stmt = stmt.where(Session.tenant_id == tenant_id)
    return round(_as_float(db.execute(stmt).scalar()), 2)


def get_overview(
    db: DbSession,
    *,
    tenant_id: int | None,
    days: int = 30,
) -> dict:
    """生成租户或平台运营总览；tenant_id=None 只允许由平台鉴权端点传入。"""
    now = _utcnow()
    since = now - timedelta(days=days - 1)
    events, event_store_available = _load_events(tenant_id, since)
    labels = _day_labels(since, now)
    resolution = _resolution_trend(events, labels)
    approval_distribution, approval_average = _approval_latency(events)

    resolved_auto = sum(item["auto"] for item in resolution)
    resolved_human = sum(item["human"] for item in resolution)
    resolution_total = resolved_auto + resolved_human
    mcp_events = [event for event in events if event.get("event_name") == "mcp_tool_called"]
    mcp_success = sum(1 for event in mcp_events if event.get("properties", {}).get("success"))

    recent = []
    for event in events[:20]:
        occurred = event.get("occurred_at")
        recent.append(
            {
                "event_name": event.get("event_name", ""),
                "tenant_id": event.get("tenant_id"),
                "properties": event.get("properties", {}),
                "occurred_at": occurred.isoformat() + "Z" if isinstance(occurred, datetime) else str(occurred or ""),
            }
        )

    return {
        "scope": "platform" if tenant_id is None else "tenant",
        "days": days,
        "generated_at": now.isoformat() + "Z",
        "event_store_available": event_store_available,
        "summary": {
            "event_count": len(events),
            "auto_resolution_rate": round(resolved_auto / resolution_total * 100, 1) if resolution_total else 0,
            "average_csat": _average_csat(db, tenant_id, since),
            "mcp_success_rate": round(mcp_success / len(mcp_events) * 100, 1) if mcp_events else 0,
            "approval_latency_seconds": approval_average,
            "security_blocks": sum(1 for event in events if event.get("event_name") == "security_block"),
        },
        "retrieval_comparison": _retrieval_comparison(events),
        "resolution_trend": resolution,
        "approval_latency_distribution": approval_distribution,
        "plan_distribution": _plan_distribution(db, tenant_id),
        "revenue_trend": _revenue_trend(db, tenant_id, since),
        "recent_events": recent,
    }
