"""
backend/app/tasks/cleanup_tasks.py

职责: 空闲会话定时收尾（007-session-lifecycle US2）、退款超时取消、文档过期摘除、
      订单超时流转（PRD 3.6）、试用版到期降级只读（PRD 3.1）

核心设计:
  后台线程每 5min 扫描，用 Core UPDATE WHERE 条件原子更新状态。
  条件中带 updated_at < threshold，用户并发消息会先刷新 updated_at 使 WHERE 不匹配 -> 跳过（防竞态）。

状态流转（007 session lifecycle）:
  AGENT_HANDLING / WAITING_HUMAN（30min 无活动）-> RESOLVED
  RESOLVED（60min 无活动，即收尾后约 30min）-> CLOSED

订单超时流转（PRD 3.6）:
  created（30min 未支付）-> cancelled
  shipped（7 天未确认收货）-> completed

试用版降级（PRD 3.1）:
  trial 租户 created_at 超 30 天 且 status=active -> readonly
  仅阻断新会话，进行中会话由 cleanup_idle_sessions 走完（降级不中断）

关键技术点:
  - 空闲超时状态跃迁: Core UPDATE WHERE updated_at < threshold 是原子条件更新。
    用户在阈值边缘发消息会刷新 updated_at，WHERE 条件不满足 -> 不跃迁（防竞态）。
    不 bump updated_at（Core update 不触发 ORM onupdate）:
    RESOLVED 的 updated_at 仍是最后活动时间，
    60min 阈值 = 收尾点(30min) + 再 30min -> CLOSED，恰好"RESOLVED 再过 30min"。
  - 退款超时取消: pending 状态的退款审批超 7 天 -> cancelled，避免无限期挂起。
  - 文档过期摘除: expires_at < now 且未标记 expired 的文档 -> quality_status=expired，
    检索时过滤 expired 文档实现知识"摘除"。
  - 订单超时: created/shipped 状态 updated_at 越过阈值 -> cancelled/completed。
    状态值用 Order.VALID_TRANSITIONS 的小写字符串，不用大写枚举名。
  - 试用版降级: UPDATE tenants SET status=readonly WHERE plan_type=trial AND status=active
    AND created_at < threshold，原子条件更新防误降级；只改租户状态不碰会话表。

依赖关系:
  - 上游: app/main.py startup（后台线程定时调用）
  - 下游: app.models.session/refund_approval/knowledge_document/order/tenant
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session as DbSession

from app.models.session import Session

logger = logging.getLogger(__name__)

ACTIVE_TIMEOUT_MINUTES = 30   # AGENT_HANDLING/WAITING_HUMAN -> RESOLVED 阈值
RESOLVED_TIMEOUT_MINUTES = 60  # RESOLVED -> CLOSED 阈值（= 30min 活动 + 30min RESOLVED 窗）


def _utcnow() -> datetime:
    """返回 naive UTC datetime（与 DB server_default=func.now() 的 naive UTC 一致，可直接比较）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cleanup_idle_sessions(db: DbSession) -> dict:
    """收尾空闲会话。返回 {"resolved": N, "closed": N}。

    两阶段状态跃迁:
      阶段1: agent_handling/waiting_human 且 updated_at < now-30min -> resolved
      阶段2: resolved 且 updated_at < now-60min -> closed

    防竞态: Core UPDATE WHERE updated_at < threshold 是原子操作。
    用户在阈值边缘发消息会先刷新 updated_at，WHERE 不匹配 -> 跳过（不会误跃迁）。
    不 bump updated_at: Core update 不触发 ORM onupdate，RESOLVED 的 updated_at 仍是
    最后活动时间，故 60min 阈值 = 活动 30min + RESOLVED 窗 30min，恰好"RESOLVED 再过 30min"。

    Args:
        db: SQLAlchemy Session（由调用方管理事务）

    Returns:
        {"resolved": int, "closed": int} 各阶段影响行数
    """
    now = _utcnow()
    active_threshold = now - timedelta(minutes=ACTIVE_TIMEOUT_MINUTES)
    resolved_threshold = now - timedelta(minutes=RESOLVED_TIMEOUT_MINUTES)

    # 阶段1: 活跃态 -> RESOLVED（updated_at < 30min 前表示 30min 无活动）
    r1 = db.execute(
        update(Session)
        .where(
            Session.status.in_(["agent_handling", "waiting_human"]),
            Session.updated_at < active_threshold,
        )
        .values(status="resolved")
    )

    # 阶段2: RESOLVED -> CLOSED（updated_at < 60min 前，= 活动 30min + RESOLVED 30min）
    r2 = db.execute(
        update(Session)
        .where(
            Session.status == "resolved",
            Session.updated_at < resolved_threshold,
        )
        .values(status="closed")
    )

    db.commit()
    resolved_n = r1.rowcount or 0
    closed_n = r2.rowcount or 0
    if resolved_n or closed_n:
        logger.info(f"Cleanup: {resolved_n} -> resolved, {closed_n} -> closed")
    return {"resolved": resolved_n, "closed": closed_n}


def cleanup_overdue_refunds(db: DbSession) -> dict:
    """撤销售后超时未审批的退款（010 US1，pending 超 7 天 -> cancelled）。

    防止退款申请无限期挂起，超时自动取消释放配额。
    T21: 同步把订单 CAS 回 paid——原实现只取消审批，订单永久停 refunding
    （不能重新支付、不能退款、不能流转），与手动撤销 cancel_refund 的语义对齐。
    延迟 import: 避免模块加载时循环依赖。

    Args:
        db: SQLAlchemy Session

    Returns:
        {"refunds_cancelled": int} 取消的退款数
    """
    from app.config import settings
    from app.models.order import Order
    from app.models.refund_approval import RefundApproval
    from app.crud import order as order_crud
    from sqlalchemy import select

    threshold = _utcnow() - timedelta(days=settings.refund_timeout_days)
    approvals = db.execute(
        select(RefundApproval).where(
            RefundApproval.status == "pending",
            RefundApproval.created_at < threshold,
        )
    ).scalars().all()

    cancelled = 0
    for ap in approvals:
        # 订单先 CAS 回 paid（与手动撤销一致）；失败说明订单已被并发推进
        # （如已 refunded），该审批留给正常流程，不取消
        order = db.get(Order, ap.order_id)
        if order is not None and order.status == "refunding":
            if not order_crud.update_status_optimistic(db, order, "paid"):
                continue
        # 审批条件更新（WHERE status=pending）防并发 approve 已处理被覆盖
        res = db.execute(
            update(RefundApproval)
            .where(RefundApproval.id == ap.id, RefundApproval.status == "pending")
            .values(status="cancelled")
        )
        if res.rowcount:
            cancelled += 1
    db.commit()
    if cancelled:
        logger.info(f"Cleanup: {cancelled} overdue refunds -> cancelled, orders back to paid")
    return {"refunds_cancelled": cancelled}


def cleanup_expired_docs(db: DbSession) -> dict:
    """到期知识文档摘除（010 US3，expires_at < now -> quality_status=expired）。

    摘除而非仅标记（T22）: 检索切片无 quality_status 字段，标记 expired 后必须
    物理删除 Milvus/ES 切片（复用版本管理的删除模式），否则到期文档仍被召回。
    原始文档记录保留（MySQL），便于审计/恢复。已标记 expired 的不重复处理。

    删除失败降级: warning 记日志，下轮 cleanup（5min）重试——删除幂等，
    doc_id 不存在时 delete/delete_by_query 无害。

    Args:
        db: SQLAlchemy Session

    Returns:
        {"docs_expired": int} 标记过期的文档数
    """
    from app.models.knowledge_document import KnowledgeDocument
    from sqlalchemy import select

    now = _utcnow()
    docs = db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.expires_at.isnot(None),
            KnowledgeDocument.expires_at < now,
            KnowledgeDocument.quality_status != "expired",
        )
    ).scalars().all()

    expired = 0
    for doc in docs:
        # 先物理删除检索库切片（Milvus + ES），成功后才标记 expired：
        # 若先标 expired 再删，删除失败时 expired 已提交，下轮 WHERE quality_status != 'expired'
        # 会排除该 doc -> 永不重试，切片残留仍可被召回。删除幂等，失败下轮重试。
        try:
            _delete_doc_chunks(doc.id)
            doc.quality_status = "expired"
            expired += 1
        except Exception as e:
            logger.warning(f"Delete expired doc chunks failed (doc={doc.id}, will retry next round): {e}")
    db.commit()
    if expired:
        logger.info(f"Cleanup: {expired} expired docs -> marked expired + chunks removed")
    return {"docs_expired": expired}


def _delete_doc_chunks(doc_id: int) -> None:
    """物理删除文档切片（Milvus + ES），幂等（doc_id 不存在时无害）。"""
    from app.core.embedding_client import get_es_client, get_milvus_collection

    collection = get_milvus_collection()
    collection.delete(expr=f"doc_id == {doc_id}")
    es = get_es_client()
    es.delete_by_query(
        index="knowledge_chunks",
        body={"query": {"term": {"doc_id": doc_id}}},
        refresh=True,
    )


def cleanup_expired_orders(db: DbSession) -> dict:
    """订单超时自动流转（PRD 3.6）。

    两类超时:
      - created 且 updated_at < now-30min -> cancelled（待支付超时取消）
      - shipped 且 updated_at < now-7天 -> completed（发货后自动完成）

    防竞态: Core UPDATE WHERE updated_at < threshold 原子更新。
      任意对订单的操作（支付/确认收货等）会刷新 updated_at，WHERE 不匹配 -> 跳过。
    状态值: 与 Order.VALID_TRANSITIONS 一致（created/cancelled/shipped/completed），
      不使用大写枚举名。

    Args:
        db: SQLAlchemy Session

    Returns:
        {"orders_cancelled": int, "orders_completed": int} 各分支影响行数
    """
    from app.config import settings
    from app.models.order import Order

    now = _utcnow()
    payment_threshold = now - timedelta(minutes=settings.order_payment_timeout_minutes)
    shipped_threshold = now - timedelta(days=settings.order_shipped_timeout_days)

    r1 = db.execute(
        update(Order)
        .where(
            Order.status == "created",
            Order.updated_at < payment_threshold,
        )
        .values(status="cancelled")
    )
    r2 = db.execute(
        update(Order)
        .where(
            Order.status == "shipped",
            Order.updated_at < shipped_threshold,
        )
        .values(status="completed")
    )
    db.commit()
    cancelled_n = r1.rowcount or 0
    completed_n = r2.rowcount or 0
    if cancelled_n or completed_n:
        logger.info(f"Cleanup: {cancelled_n} orders -> cancelled, {completed_n} orders -> completed")
    return {"orders_cancelled": cancelled_n, "orders_completed": completed_n}


def cleanup_trial_degradation(db: DbSession) -> dict:
    """试用版到期降级只读（PRD 3.1）。

    trial 租户 created_at 超 30 天 且 status=active -> status=readonly。

    降级不中断: 仅更新租户状态字段阻断"新会话"创建，进行中会话（agent_handling/
    waiting_human/human_handling）由 cleanup_idle_sessions 按其自身超时走完。
    防竞态: Core UPDATE WHERE created_at < threshold AND plan_type=trial AND status=active
    原子更新；已 closed/readonly 的租户 WHERE 不匹配 -> 跳过。

    Args:
        db: SQLAlchemy Session

    Returns:
        {"tenants_degraded": int} 降级的租户数
    """
    from app.config import settings
    from app.models.tenant import Tenant

    threshold = _utcnow() - timedelta(days=settings.trial_period_days)
    r = db.execute(
        update(Tenant)
        .where(
            Tenant.plan_type == "trial",
            Tenant.status == "active",
            Tenant.created_at < threshold,
        )
        .values(status="readonly")
    )
    db.commit()
    n = r.rowcount or 0
    if n:
        logger.info(f"Cleanup: {n} trial tenants -> readonly")
    return {"tenants_degraded": n}
