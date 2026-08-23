"""
backend/app/tasks/cleanup_tasks.py

职责: 空闲会话定时收尾（规格 007 US2）、退款超时取消、文档过期摘除、
      订单超时流转（PRD 3.6）、试用版到期降级只读（PRD 3.1）

核心设计:
  后台线程每 5min 扫描，用 Core UPDATE WHERE 条件原子更新状态。
  条件中带 updated_at < threshold，用户并发消息会先刷新 updated_at 使 WHERE 不匹配 -> 跳过（防竞态）。

状态流转（规格 007 session lifecycle）:
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
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery import shared_task
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session as DbSession

from app.models.session import Session

logger = logging.getLogger(__name__)

ACTIVE_TIMEOUT_MINUTES = 30   # AGENT_HANDLING/WAITING_HUMAN -> RESOLVED 阈值
RESOLVED_TIMEOUT_MINUTES = 60  # RESOLVED -> CLOSED 阈值（= 30min 活动 + 30min RESOLVED 窗）


def _utcnow() -> datetime:
    """返回 naive UTC datetime（与 DB server_default=func.now() 的 naive UTC 一致，可直接比较）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@shared_task(name="app.tasks.cleanup_tasks.run_periodic_maintenance")
def run_periodic_maintenance() -> dict:
    """Celery beat 周期维护入口；每次任务独立创建并关闭数据库会话。"""
    from app.core.database import SessionLocal
    from app.services.order_service import process_refund_outbox_batch

    db = SessionLocal()
    try:
        return {
            "tenant_cancellations": cleanup_expired_tenant_cancellations(db),
            "sessions": cleanup_idle_sessions(db),
            "refunds": cleanup_overdue_refunds(db),
            "orders": cleanup_expired_orders(db),
            "documents": cleanup_expired_docs(db),
            "trials": cleanup_trial_degradation(db),
            "presence": cleanup_agent_presence(),
            "reminders": remind_expiring_docs(db),
            "refund_outbox": process_refund_outbox_batch(db),
        }
    finally:
        db.close()


def cleanup_expired_tenant_cancellations(
    db: DbSession, *, now: datetime | None = None,
) -> dict:
    """物理删除已超过 30 天导出窗口的租户数据（PRD §8.5）。

    外部存储先删、关系库后删；任一外部存储失败则保留 MySQL 租户行并在下一轮重试。
    所有删除均按 tenant_id/doc_id/session_id 精确限定且具备幂等性。共享平台账号仅在
    已不属于任何其他租户且没有剩余外键引用时删除。
    """

    current = now or _utcnow()
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    from app.models.tenant import Tenant

    tenant_ids = list(
        db.execute(
            select(Tenant.id).where(
                Tenant.status == "pending_deletion",
                Tenant.scheduled_deletion_at.isnot(None),
                Tenant.scheduled_deletion_at <= current,
            )
        ).scalars().all()
    )
    deleted = 0
    failed = 0
    for tenant_id in tenant_ids:
        try:
            if _purge_expired_tenant(db, tenant_id=tenant_id, now=current):
                deleted += 1
        except Exception as exc:
            db.rollback()
            failed += 1
            logger.exception(
                "Tenant physical purge failed; retained for retry (tenant=%s): %s",
                tenant_id,
                exc,
            )
    if deleted:
        logger.info("Cleanup: %s expired tenant cancellations physically deleted", deleted)
    return {"tenants_deleted": deleted, "tenants_failed": failed}


def _purge_expired_tenant(db: DbSession, *, tenant_id: int, now: datetime) -> bool:
    """锁定并复核截止时间后，执行单租户跨存储物理删除。"""

    from app.models.billing_event import BillingEvent
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.order import Order
    from app.models.session import Session
    from app.models.tenant import Tenant
    from app.models.tenant_member import TenantMember

    tenant = (
        db.query(Tenant)
        .filter(Tenant.id == tenant_id)
        .with_for_update()
        .first()
    )
    if (
        tenant is None
        or tenant.status != "pending_deletion"
        or tenant.scheduled_deletion_at is None
        or tenant.scheduled_deletion_at > now
    ):
        db.rollback()
        return False

    documents = list(
        db.execute(
            select(KnowledgeDocument.id, KnowledgeDocument.filename).where(
                KnowledgeDocument.tenant_id == tenant_id
            )
        ).all()
    )
    session_ids = list(
        db.execute(select(Session.id).where(Session.tenant_id == tenant_id)).scalars().all()
    )
    order_ids = list(
        db.execute(select(Order.id).where(Order.tenant_id == tenant_id)).scalars().all()
    )
    user_ids = list(
        db.execute(
            select(TenantMember.user_id).where(TenantMember.tenant_id == tenant_id)
        ).scalars().all()
    )
    billing_event_keys = list(
        db.execute(
            select(BillingEvent.event_key).where(BillingEvent.tenant_id == tenant_id)
        ).scalars().all()
    )

    _purge_tenant_external_data(
        tenant_id=tenant_id,
        documents=documents,
        session_ids=session_ids,
        order_ids=order_ids,
        billing_event_keys=billing_event_keys,
    )
    _purge_tenant_relational_data(
        db,
        tenant_id=tenant_id,
        session_ids=session_ids,
        order_ids=order_ids,
        user_ids=user_ids,
    )
    db.commit()
    return True


def _purge_tenant_external_data(
    *,
    tenant_id: int,
    documents: list,
    session_ids: list[int],
    order_ids: list[int],
    billing_event_keys: list[str],
) -> None:
    """删除 MongoDB、Milvus、ES、Redis 与上传源文件中的租户数据。"""

    from app.config import settings

    # MongoDB：Agent 轨迹与 DeepAgent 任务均直接带 tenant_id。
    from pymongo import MongoClient

    mongo = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        mongo.admin.command("ping")
        mongo["zhihub"]["agent_traces"].delete_many({"tenant_id": tenant_id})
        mongo["zhihub"]["deep_agent_tasks"].delete_many({"tenant_id": tenant_id})
        mongo["zhihub"]["analytics_events"].delete_many({"tenant_id": tenant_id})
    finally:
        mongo.close()

    # Milvus：知识切片和长期记忆都以 tenant_id 为分区键。
    from pymilvus import Collection, utility
    from app.core.embedding_client import (
        COLLECTION_NAME,
        MEMORY_COLLECTION_NAME,
        _connect_milvus,
        get_es_client,
    )

    _connect_milvus()
    for collection_name in (COLLECTION_NAME, MEMORY_COLLECTION_NAME):
        if utility.has_collection(collection_name):
            collection = Collection(collection_name)
            collection.delete(expr=f"tenant_id == {tenant_id}")
            collection.flush()

    # Elasticsearch：知识切片按 tenant_id 删除；索引尚未创建时视为空数据。
    es = get_es_client()
    if es.indices.exists(index="knowledge_chunks"):
        es.delete_by_query(
            index="knowledge_chunks",
            body={"query": {"term": {"tenant_id": tenant_id}}},
            refresh=True,
        )

    _purge_tenant_redis_data(
        tenant_id=tenant_id,
        session_ids=session_ids,
        order_ids=order_ids,
        billing_event_keys=billing_event_keys,
    )
    _delete_tenant_upload_files(documents)


def _purge_tenant_redis_data(
    *,
    tenant_id: int,
    session_ids: list[int],
    order_ids: list[int],
    billing_event_keys: list[str],
) -> None:
    """清理所有可归属到租户、会话或订单的 Redis 临时状态。"""

    from app.core.redis import get_redis

    redis_client = get_redis()
    patterns = [
        f"billing:quota:{tenant_id}:*",
        f"billing:warning:{tenant_id}:*",
        f"warning_banner:{tenant_id}:*",
        f"billing:usage_last:{tenant_id}:*",
        f"answer_cache:{tenant_id}:*",
        f"agents:online:{tenant_id}:*",
        f"idempotency:{tenant_id}:*",
        f"idempotency:processing:{tenant_id}:*",
    ]
    exact_keys = [
        f"billing:degraded:{tenant_id}",
        f"degradation:{tenant_id}",
        f"rate:tenant:{tenant_id}:minute",
        f"notif:count:quota_warning:{tenant_id}",
    ]
    for session_id in session_ids:
        exact_keys.extend(
            [
                f"session:context:{session_id}",
                f"session:rounds:{session_id}",
                f"claim:session:{session_id}",
                f"rate:widget:{session_id}:minute",
            ]
        )
    for namespace in order_ids:
        patterns.extend(
            [
                f"idempotency:{namespace}:*",
                f"idempotency:processing:{namespace}:*",
            ]
        )
    exact_keys.extend(f"billing:event:{event_key}" for event_key in billing_event_keys)

    matched: set[str] = set(exact_keys)
    for pattern in patterns:
        matched.update(redis_client.scan_iter(match=pattern))

    # 邀请 key 的 token 不含 tenant_id，需检查其 JSON value 后精确删除。
    for key in redis_client.scan_iter(match="invite:*"):
        raw = redis_client.get(key)
        try:
            if raw and int(json.loads(raw).get("tenant_id")) == tenant_id:
                matched.add(key)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    if matched:
        redis_client.delete(*matched)


def _delete_tenant_upload_files(documents: list) -> None:
    """按已查得 doc_id + basename 删除共享上传目录和旧临时目录源文件。"""

    from app.config import settings

    roots = [Path(settings.upload_storage_dir).resolve(), Path(tempfile.gettempdir(), "zhihub_uploads").resolve()]
    for doc_id, filename in documents:
        basename = os.path.basename(filename)
        for root in roots:
            path = (root / f"{doc_id}_{basename}").resolve()
            if path.parent != root:
                raise RuntimeError(f"拒绝删除上传目录之外的路径: {path}")
            if path.is_file():
                path.unlink()


def _purge_tenant_relational_data(
    db: DbSession,
    *,
    tenant_id: int,
    session_ids: list[int],
    order_ids: list[int],
    user_ids: list[int],
) -> None:
    """按外键从叶子到根删除 MySQL 数据，并保留仍属于其他租户的共享用户。"""

    from app.models.answer_feedback import AnswerFeedback
    from app.models.audit_log import AuditLog
    from app.models.billing_event import BillingEvent
    from app.models.billing_invoice import BillingInvoice
    from app.models.csat import CsatEvaluation
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.message import Message
    from app.models.notification import Notification
    from app.models.order import Order
    from app.models.quick_reply import QuickReply
    from app.models.refresh_token import RefreshToken
    from app.models.refund_approval import RefundApproval
    from app.models.refund_approval_event import RefundApprovalEvent
    from app.models.refund_outbox import RefundOutbox
    from app.models.security_incident import SecurityIncident
    from app.models.session import Session
    from app.models.tenant import Tenant
    from app.models.tenant_member import TenantMember
    from app.models.ticket import Ticket
    from app.models.usage_record import UsageRecord
    from app.models.user import User

    message_ids = list(
        db.execute(select(Message.id).where(Message.session_id.in_(session_ids))).scalars().all()
    ) if session_ids else []
    approval_ids = list(
        db.execute(
            select(RefundApproval.id).where(RefundApproval.order_id.in_(order_ids))
        ).scalars().all()
    ) if order_ids else []

    mysql_guard = db.bind is not None and db.bind.dialect.name == "mysql"
    if mysql_guard:
        db.execute(text("SET @zhihub_tenant_purge = 1"))
    try:
        if message_ids:
            db.execute(delete(AnswerFeedback).where(AnswerFeedback.message_id.in_(message_ids)))
        if session_ids:
            db.execute(delete(CsatEvaluation).where(CsatEvaluation.session_id.in_(session_ids)))
            db.execute(delete(BillingEvent).where(BillingEvent.session_id.in_(session_ids)))
            db.execute(delete(SecurityIncident).where(SecurityIncident.session_id.in_(session_ids)))
            db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
            db.execute(delete(Session).where(Session.id.in_(session_ids)))
        if approval_ids:
            db.execute(delete(RefundOutbox).where(RefundOutbox.approval_id.in_(approval_ids)))
            db.execute(
                delete(RefundApprovalEvent).where(
                    RefundApprovalEvent.approval_id.in_(approval_ids)
                )
            )
            db.execute(delete(RefundApproval).where(RefundApproval.id.in_(approval_ids)))
        if order_ids:
            db.execute(delete(Order).where(Order.id.in_(order_ids)))

        db.execute(delete(Ticket).where(Ticket.tenant_id == tenant_id))
        db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        db.execute(delete(Notification).where(Notification.tenant_id == tenant_id))
        db.execute(delete(QuickReply).where(QuickReply.tenant_id == tenant_id))
        db.execute(delete(BillingInvoice).where(BillingInvoice.tenant_id == tenant_id))
        db.execute(delete(UsageRecord).where(UsageRecord.tenant_id == tenant_id))
        db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id))
        # refresh_tokens 是自引用链，先断开 parent 再批量删除。
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.tenant_id == tenant_id)
            .values(parent_token_id=None)
        )
        db.execute(delete(RefreshToken).where(RefreshToken.tenant_id == tenant_id))
        db.execute(delete(TenantMember).where(TenantMember.tenant_id == tenant_id))
        db.flush()

        for user_id in set(user_ids):
            if not _user_has_remaining_references(db, user_id):
                db.execute(delete(User).where(User.id == user_id))
        db.execute(delete(Tenant).where(Tenant.id == tenant_id))
    finally:
        if mysql_guard:
            db.execute(text("SET @zhihub_tenant_purge = 0"))


def _user_has_remaining_references(db: DbSession, user_id: int) -> bool:
    """判断平台用户是否仍被其他租户或业务记录引用。"""

    from app.models.audit_log import AuditLog
    from app.models.refresh_token import RefreshToken
    from app.models.refund_approval import RefundApproval
    from app.models.refund_approval_event import RefundApprovalEvent
    from app.models.session import Session
    from app.models.tenant_member import TenantMember
    from app.models.ticket import Ticket

    checks = (
        select(TenantMember.id).where(TenantMember.user_id == user_id),
        select(RefreshToken.id).where(RefreshToken.user_id == user_id),
        select(AuditLog.id).where(AuditLog.user_id == user_id),
        select(Session.id).where(Session.assignee_id == user_id),
        select(Ticket.id).where(Ticket.assignee_id == user_id),
        select(RefundApproval.id).where(RefundApproval.approver_id == user_id),
        select(RefundApprovalEvent.id).where(RefundApprovalEvent.actor_id == user_id),
    )
    return any(db.execute(query.limit(1)).scalar_one_or_none() is not None for query in checks)


def cleanup_agent_presence() -> dict:
    """清理无 TTL 或旧 Set 结构的客服 presence；正常过期由 Redis 自身处理。"""
    from app.services.session_service import cleanup_expired_agent_presence

    removed = cleanup_expired_agent_presence()
    return {"presence_removed": removed}


def cleanup_idle_sessions(db: DbSession) -> dict:
    """收尾空闲会话。返回 {"resolved": N, "closed": N}。

    两阶段状态跃迁:
      阶段1: agent_handling/waiting_human/human_handling 且 updated_at < now-30min -> resolved
      阶段2: resolved 且 updated_at < now-60min -> closed

     计费口径（PRD 3.4/3.9）: 会话进入 RESOLVED 且存在有效回复（effective_reply=True）
    时，在同一数据库事务原子领取并创建计费事件；Redis 投影失败由 pending 事件重试。

    防竞态: 阶段2 用 Core UPDATE WHERE updated_at < threshold 原子更新；
    阶段1 用 UPDATE WHERE billed=false 原子领取，billed 与计费事件在同一事务提交。
    不 bump updated_at: ORM 赋值不触发 onupdate（Core update 同样不触发），
    RESOLVED 的 updated_at 仍是最后活动时间，故 60min 阈值 = 活动 30min + RESOLVED 窗 30min。

    Args:
        db: SQLAlchemy Session（由调用方管理事务）

    Returns:
        {"resolved": int, "closed": int} 各阶段影响行数
    """
    now = _utcnow()
    active_threshold = now - timedelta(minutes=ACTIVE_TIMEOUT_MINUTES)
    resolved_threshold = now - timedelta(minutes=RESOLVED_TIMEOUT_MINUTES)

    # 阶段1: 活跃态（含 human_handling）→ RESOLVED（updated_at < 30min 前表示 30min 无活动）
    from sqlalchemy import select
    candidates = db.execute(
        select(Session).where(
            Session.status.in_(["agent_handling", "waiting_human", "human_handling"]),
            Session.updated_at < active_threshold,
        )
    ).scalars().all()

    from app.crud import billing as billing_crud
    from app.services.billing_service import (
        consume_pending_billing_events,
        create_session_billing_event,
    )

    resolved_n = 0
    # 记录本轮回收到 RESOLVED 的会话 id，事务提交后向终端用户广播 session_ended
    # （widget.js 据此展示"会话已结束"并停止重连；fire-and-forget，不阻塞清理）。
    ended_session_ids: list[int] = []
    resolved_events: dict[int, tuple[int, dict]] = {}
    for s in candidates:
        resolution_mode = "human" if s.assignee_id or s.status in {"waiting_human", "human_handling"} else "auto"
        event_properties = {
            "session_id": s.id,
            "resolution_mode": resolution_mode,
            "previous_status": s.status,
            "intent": s.intent or "unknown",
        }
        # 有效回复：状态跃迁与 billed=false 领取合并为一条条件 UPDATE。
        if s.effective_reply and not s.billed:
            if billing_crud.claim_idle_session_for_billing(
                db,
                session_id=s.id,
                active_threshold=active_threshold,
            ):
                create_session_billing_event(db, s)
                resolved_n += 1
                ended_session_ids.append(s.id)
                resolved_events[s.id] = (s.tenant_id, event_properties)
            continue

        # 无有效回复或已经有事件的会话只做原子状态跃迁，不创建计费事件。
        transition = db.execute(
            update(Session)
            .where(
                Session.id == s.id,
                Session.status.in_(["agent_handling", "waiting_human", "human_handling"]),
                Session.updated_at < active_threshold,
            )
            .values(status="resolved")
        )
        resolved_n += transition.rowcount or 0
        if transition.rowcount:
            ended_session_ids.append(s.id)
            resolved_events[s.id] = (s.tenant_id, event_properties)

    # 升级兼容：旧流程 Redis 失败后可能留下 resolved/closed + billed=false，
    # 不再依赖 resolved 扫描执行计费，而是为其补建事件后交给统一消费者。
    legacy_unbilled = db.execute(
        select(Session).where(
            Session.status.in_(["resolved", "closed"]),
            Session.effective_reply.is_(True),
            Session.billed.is_(False),
        )
    ).scalars().all()
    for s in legacy_unbilled:
        if billing_crud.claim_legacy_resolved_session(db, s.id):
            create_session_billing_event(db, s)

    # 阶段2: RESOLVED → CLOSED（updated_at < 60min 前，= 活动 30min + RESOLVED 30min）
    r2 = db.execute(
        update(Session)
        .where(
            Session.status == "resolved",
            Session.updated_at < resolved_threshold,
        )
        .values(status="closed")
    )

    db.commit()
    from app.services.analytics_service import track_event
    for _resolved_id, (event_tenant_id, properties) in resolved_events.items():
        track_event(
            "session_resolved",
            tenant_id=event_tenant_id,
            properties=properties,
        )
    # 事件提交后再投影 Redis；失败事件保持 pending，下轮与会话状态无关地重试。
    consume_pending_billing_events(db)
    # 向终端用户广播会话结束（事务已提交，状态跃迁生效；无连接时 no-op）
    from app.core.ws_manager import manager as _ws_manager

    for ended_id in ended_session_ids:
        _ws_manager.broadcast_to_session_sync(
            ended_id,
            {
                "type": "session_ended",
                "payload": {"session_id": ended_id, "status": "resolved"},
            },
        )
    closed_n = r2.rowcount or 0
    if resolved_n or closed_n:
        logger.info(f"Cleanup: {resolved_n} -> resolved, {closed_n} -> closed")
    return {"resolved": resolved_n, "closed": closed_n}


def remind_expiring_docs(db: DbSession) -> dict:
    """知识库文档到期前 7 天邮件提醒（PRD 3.2 生命周期治理）。

    扫描 expires_at 位于 [now, now+7d] 且未过期的文档，向租户管理员（有邮箱者）发送提醒邮件；
    每文档成功提醒一次（Redis 去重键，TTL 8 天），发送失败/无管理员邮箱时短冷却后重试。
    """
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.tenant_member import TenantMember
    from app.models.user import User
    from sqlalchemy import select

    from app.core.redis import get_redis

    now = _utcnow()
    horizon = now + timedelta(days=7)
    docs = db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.expires_at.isnot(None),
            KnowledgeDocument.expires_at >= now,
            KnowledgeDocument.expires_at <= horizon,
            KnowledgeDocument.quality_status != "expired",
        )
    ).scalars().all()

    reminded = 0
    r = get_redis()
    for doc in docs:
        key = f"doc_expiry_remind:{doc.id}"
        if r.exists(key):
            continue

        emails = [
            e for e in db.execute(
                select(User.email)
                .join(TenantMember, TenantMember.user_id == User.id)
                .where(
                    TenantMember.tenant_id == doc.tenant_id,
                    TenantMember.role == "admin",
                    User.email.isnot(None),
                )
            ).scalars().all() if e
        ]
        if not emails:
            # 管理员暂无邮箱：短冷却后重试（补邮箱后仍可触达）
            r.set(key, "1", ex=6 * 3600)
            continue

        from app.core.email_client import EmailSendError, send_email

        sent_ok = True
        for email in emails:
            try:
                send_email(
                    to=email,
                    subject="知识库文档即将到期提醒",
                    text=(
                        f"知识库文档「{doc.filename}」将于 {doc.expires_at:%Y-%m-%d %H:%M} 到期，"
                        "到期后将自动从检索库摘除，不再参与回答。"
                        "请登录运营后台确认是否延期或更新该文档。"
                    ),
                )
            except EmailSendError as e:
                logger.warning(f"Expiry remind email failed (doc={doc.id}, to={email}): {e}")
                sent_ok = False

        if sent_ok:
            r.set(key, "1", ex=8 * 24 * 3600)
            reminded += 1
            logger.info(f"Doc expiry remind sent (doc={doc.id}, emails={len(emails)})")
        else:
            # 发送失败：短冷却后重试，避免每轮循环反复尝试
            r.set(key, "1", ex=6 * 3600)

    return {"docs_reminded": reminded}


def cleanup_overdue_refunds(db: DbSession) -> dict:
    """撤销售后超时未审批的退款（规格 010 US1，pending 超 7 天 -> cancelled）。

    防止退款申请无限期挂起，超时自动取消释放配额。
    : 同步把订单 CAS 回 paid——原实现只取消审批，订单永久停 refunding
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
            restore_status = ap.pre_status or "paid"
            if not order_crud.update_status_optimistic(db, order, restore_status):
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
    """到期知识文档摘除（规格 010 US3，expires_at < now -> quality_status=expired）。

    摘除而非仅标记: 检索切片无 quality_status 字段，标记 expired 后必须
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
        # 会排除该 doc → 永不重试，切片残留仍可被召回。删除幂等，失败下轮重试。
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
