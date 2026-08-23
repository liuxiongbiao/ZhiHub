"""
backend/app/services/human_service.py

职责: 转人工条件汇总判断、坐席认领 Redis 分布式锁互斥、坐席退回给 Agent、
      无在线坐席时留言自动建单

核心设计:
  转人工的核心难点是"两个坐席同时认领同一会话"的并发竞争。用 Redis SETNX（SET nx=True）
  实现原子互斥锁：第一个 SET 成功的坐席获得认领权，其余直接拒绝。锁带 5 分钟 TTL 防止
  坐席异常离线后会话永久锁定。退回 Agent 时主动释放锁，让会话可被重新认领。

关键技术点:
  - Redis SETNX 原子锁: SET key val ex=TTL nx=True，单条命令完成"不存在则设置+设过期"，无竞态
  - 锁与状态分离: 先抢锁再校验状态，校验失败主动释放锁，避免锁泄漏
  - 间接租户校验: 通过 session.tenant_id 校验，而非在锁里携带 tenant_id
  - 留言建单降级: 无在线坐席时把用户消息转成 category=message 工单，保证消息不丢

依赖关系:
  - 上游: app/api/v1/session.py（leave-message / claim 端点）
  - 下游: app.core.redis（分布式锁）、app.crud.session（会话状态）、app.crud.ticket（留言建单）

关键函数:
  - claim_session(session_id, agent_id) -> bool  # Redis SETNX 原子锁
  - return_to_agent(session_id) -> None
  - leave_message_if_no_agent(session_id) -> Ticket
"""

import logging
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.core.ws_manager import manager
from app.crud import session as session_crud

logger = logging.getLogger(__name__)

# Redis 分布式锁键前缀 + TTL —— 格式 claim:session:{session_id}
CLAIM_LOCK_PREFIX = "claim:session:"
CLAIM_LOCK_TTL = 300  # 5 分钟认领锁；坐席异常离线后 5 分钟自动释放，避免会话永久锁定


class HumanHandoffError(Exception): pass
class SessionAlreadyClaimed(HumanHandoffError): pass
class AgentMessageForbidden(HumanHandoffError): pass
class AgentMessageStateInvalid(HumanHandoffError): pass


def claim_session(db: Session, *, session_id: int, agent_id: int, tenant_id: int) -> dict:
    """坐席认领会话。

    使用 Redis SETNX 保证原子性：两个坐席同时认领同一会话，只有一个成功。
    成功后更新会话 assignee_id + status=human_handling。

    流程:
      1. Redis SETNX 抢锁（5 分钟 TTL），失败说明已被其他坐席认领
      2. 加载会话 + 校验状态（仅 waiting_human / agent_handling 可认领）
      3. 更新会话状态为 human_handling，写入 assignee_id

    设计决策:
      - 先抢锁再查 DB: 锁是第一道防线，抢不到直接拒绝，避免无谓的 DB 查询
      - 校验失败主动释放锁: 会话不存在或状态不支持时，释放锁不阻塞后续认领
      - 锁 TTL 5 分钟: 平衡"坐席处理时间"与"异常恢复速度"

    Args:
        db: 数据库会话
        session_id: 会话 ID
        agent_id: 坐席 ID
        tenant_id: 租户 ID（租户隔离）

    Returns:
        dict: session_id / agent_id / status
    """
    r = get_redis()
    lock_key = f"{CLAIM_LOCK_PREFIX}{session_id}"

    # 1. 原子锁: SETNX key agent_id EX 300
    # —— nx=True 保证"不存在才设置"是原子的，两个坐席并发只有一个返回 True
    acquired = r.set(lock_key, str(agent_id), ex=CLAIM_LOCK_TTL, nx=True)
    if not acquired:
        current = r.get(lock_key)
        raise SessionAlreadyClaimed(f"会话已被坐席 {current} 认领")

    # 2. 加载会话 + 校验状态
    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        r.delete(lock_key)  # 释放锁 -- 会话不存在，不持有锁
        raise HumanHandoffError(f"会话不存在: {session_id}")
    if s.status != "waiting_human":
        r.delete(lock_key)  # 释放锁 -- 状态不支持认领
        raise HumanHandoffError(f"会话状态 {s.status} 不支持认领")

    # 3. 更新状态 —— 认领成功，写 assignee_id 并推进状态
    # 异常时释放锁避免泄漏至 TTL，保证其他坐席可认领
    try:
        session_crud.update_status(db, s, "human_handling")
        s.assignee_id = agent_id
        db.commit()
    except Exception:
        r.delete(lock_key)
        db.rollback()
        raise

    logger.info(f"Session {session_id} claimed by agent {agent_id}")
    from datetime import datetime, timezone
    from app.services.analytics_service import track_event
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    wait_duration = max(0, (now - s.created_at).total_seconds()) if s.created_at else 0
    track_event(
        "human_takeover",
        tenant_id=tenant_id,
        properties={
            "session_id": session_id,
            "agent_id": agent_id,
            "trigger_reason": "agent_claim",
            "wait_duration": round(wait_duration, 3),
        },
    )

    # PRD §3.7: 认领成功后向同租户在线坐席推送实时通知。
    # broadcast_to_tenant_sync 是 fire-and-forget，不阻塞业务；无在线坐席时直接返回。
    # 用途: 工作台其他坐席实时感知"该会话已被认领"，避免重复认领尝试。
    manager.broadcast_to_tenant_sync(
        tenant_id,
        {
            "type": "session_claimed",
            "payload": {
                "session_id": session_id,
                "agent_id": agent_id,
                "tenant_id": tenant_id,
            },
        },
    )
    # 向终端用户会话通道广播 human_claimed，widget.js 据此展示"客服已接入"。
    manager.broadcast_to_session_sync(
        session_id,
        {
            "type": "human_claimed",
            "payload": {
                "session_id": session_id,
                "agent_id": agent_id,
            },
        },
    )
    return {"session_id": session_id, "agent_id": agent_id, "status": "human_handling"}


def send_agent_message(
    db: Session,
    *,
    session_id: int,
    tenant_id: int,
    agent_id: int,
    content: str,
) -> dict:
    """当前认领客服发送消息：校验接管关系、以 agent 角色落库并实时推送终端用户。"""
    session = session_crud.get_by_id(db, session_id, tenant_id)
    if session is None:
        raise HumanHandoffError(f"会话不存在: {session_id}")
    if session.status != "human_handling":
        raise AgentMessageStateInvalid("会话不处于人工接管状态")
    if session.assignee_id != agent_id:
        raise AgentMessageForbidden("只有当前认领坐席可以回复")

    message = session_crud.create_message(
        db,
        session_id=session_id,
        role="agent",
        content=content,
    )
    db.commit()
    manager.broadcast_to_session_sync(
        session_id,
        {
            "type": "agent_message",
            "payload": {
                "session_id": session_id,
                "message_id": message.id,
                "role": "agent",
                "content": content,
            },
        },
    )
    logger.info(f"Agent {agent_id} replied to session {session_id}")
    return {
        "message_id": message.id,
        "role": "agent",
        "content": content,
        "sources": [],
        "metadata": None,
    }


def return_to_agent(db: Session, *, session_id: int, tenant_id: int, agent_id: int) -> dict:
    """坐席退回会话给 AI Agent。

    流程:
      1. 加载会话，校验存在性
      2. 校验操作者是当前认领坐席（assignee_id 匹配），防止其他坐席越权退回
      3. 状态回退为 agent_handling，清空 assignee_id
      4. 释放 Redis 认领锁，让会话可被重新认领

    设计决策:
      - 校验 assignee_id 匹配: 只有认领者本人能退回，防止坐席间互相抢占
      - 退回后释放锁: 会话回到 Agent 处理，锁不再需要

    Args:
        db: 数据库会话
        session_id: 会话 ID
        tenant_id: 租户 ID
        agent_id: 当前认领坐席 ID

    Returns:
        dict: session_id / status
    """
    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        raise HumanHandoffError(f"会话不存在: {session_id}")
    # 校验操作者是当前认领者 —— 防止其他坐席越权退回别人的会话
    if s.assignee_id != agent_id:
        raise HumanHandoffError("只有当前认领坐席可以退回")

    session_crud.update_status(db, s, "agent_handling")
    s.assignee_id = None
    # PRD §3.7：退回 Agent 时向用户端插入过渡提示
    session_crud.create_message(
        db, session_id=session_id, role="system",
        content="正在为您继续查询相关信息...",
    )
    db.commit()

    # 释放认领锁 —— 退回 Agent 后会话可被重新认领
    r = get_redis()
    r.delete(f"{CLAIM_LOCK_PREFIX}{session_id}")

    # 向终端用户会话通道广播 human_returned，widget.js 据此展示"已转回智能助手"。
    manager.broadcast_to_session_sync(
        session_id,
        {
            "type": "human_returned",
            "payload": {
                "session_id": session_id,
            },
        },
    )

    logger.info(f"Session {session_id} returned to agent")
    return {"session_id": session_id, "status": "agent_handling"}


def leave_message_if_no_agent(db: Session, *, session_id: int, tenant_id: int, user_message: str) -> dict:
    """无在线坐席时留言自动建工单（规格 007: category=message 标记留言工单）.

    注意：本函数假定调用方已判定 has_online_agent==False（见 session.py leave-message 端点）。

    流程:
      1. 生成工单号 TK-{8位hex}
      2. 创建工单，category=message 标记为留言类型
      3. 提交事务

    设计决策:
      - 留言转工单而非丢弃: 保证用户消息不丢失，坐席上线后可处理
      - category=message: 与普通工单区分，便于坐席按类型筛选
      - 不在本函数内判断在线坐席: 职责单一，调用方负责判断条件

    Args:
        db: 数据库会话
        session_id: 关联会话 ID
        tenant_id: 租户 ID
        user_message: 用户留言内容

    Returns:
        dict: ticket_no / session_id
    """
    from app.crud.ticket import create_ticket
    import uuid

    ticket_no = f"TK-{uuid.uuid4().hex[:8].upper()}"
    ticket = create_ticket(
        db, tenant_id=tenant_id, ticket_no=ticket_no,
        title=f"会话{session_id}留言", description=user_message, priority="medium",
        category="message", created_by="message",
    )
    db.commit()
    from app.services.analytics_service import track_event
    track_event(
        "message_left",
        tenant_id=tenant_id,
        properties={"session_id": session_id, "ticket_id": ticket.id, "ticket_no": ticket_no},
    )
    logger.info(f"Auto ticket {ticket_no} created for session {session_id} (category=message)")
    return {"ticket_no": ticket_no, "session_id": session_id}
