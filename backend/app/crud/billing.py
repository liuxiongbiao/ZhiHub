"""
backend/app/crud/billing.py

职责: 会话计费事件的原子领取、事件写入与待消费查询。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.models.billing_event import BillingEvent
from app.models.session import Session


def claim_idle_session_for_billing(
    db: DbSession,
    *,
    session_id: int,
    active_threshold: datetime,
) -> bool:
    """原子执行活跃态转 resolved 并领取计费权。"""
    result = db.execute(
        update(Session)
        .where(
            Session.id == session_id,
            Session.status.in_(["agent_handling", "waiting_human", "human_handling"]),
            Session.updated_at < active_threshold,
            Session.effective_reply.is_(True),
            Session.billed.is_(False),
        )
        .values(status="resolved", billed=True)
    )
    return (result.rowcount or 0) == 1


def claim_legacy_resolved_session(db: DbSession, session_id: int) -> bool:
    """为升级前已 resolved/closed 且未生成事件的会话补领计费权。"""
    result = db.execute(
        update(Session)
        .where(
            Session.id == session_id,
            Session.status.in_(["resolved", "closed"]),
            Session.effective_reply.is_(True),
            Session.billed.is_(False),
        )
        .values(billed=True)
    )
    return (result.rowcount or 0) == 1


def create_event(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    model: str,
    tokens: int,
    usage_amount: Decimal,
) -> BillingEvent:
    event = BillingEvent(
        session_id=session_id,
        tenant_id=tenant_id,
        event_key=f"session:{session_id}",
        model=model,
        tokens=tokens,
        usage_amount=usage_amount,
        status="pending",
    )
    db.add(event)
    return event


def list_pending_events(db: DbSession, limit: int = 100) -> list[BillingEvent]:
    return list(
        db.execute(
            select(BillingEvent)
            .where(BillingEvent.status == "pending")
            .order_by(BillingEvent.id)
            .limit(limit)
        ).scalars().all()
    )

