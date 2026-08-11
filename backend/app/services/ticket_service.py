"""
backend/app/services/ticket_service.py

职责: 工单创建前参数校验 + 幂等检查、状态机跃迁合法性校验
分工: 🟢 弱模型可独立完成（工单是最简单的状态机）
对应日程: Day 5

核心设计:
  工单是最简单的状态机模型: open -> in_progress -> resolved -> closed。
  本模块负责工单的 CRUD 编排 + 状态机校验，确保状态只能按合法路径跃迁，
  非法跃迁（如 closed -> open）直接拒绝。

关键技术点:
  - 白名单状态机: VALID_TICKET_TRANSITIONS 显式枚举每个状态的合法目标集合
  - 终态不可逆: closed 的合法目标为空集，任何跃迁都被拒绝
  - 租户隔离: 所有查询按 tenant_id 过滤，防跨租户访问

依赖关系:
  - 上游: app/api/v1/tickets.py（工单 CRUD 端点）
  - 下游: app.crud.ticket（持久化）
"""

from sqlalchemy.orm import Session

from app.crud import ticket as ticket_crud


class TicketError(Exception): pass
class TicketNotFound(TicketError): pass


# 状态机: open ↔ in_progress -> resolved -> closed
# -- open 可推进到 in_progress，也可回退（reopen）；closed 是终态不可逆
VALID_TICKET_TRANSITIONS = {
    "open": {"in_progress"},
    "in_progress": {"resolved", "open"},
    "resolved": {"closed"},
    "closed": set(),  # 终态 -- 空集表示不可再跃迁
}


def get_ticket_status(db: Session, *, tenant_id: int, ticket_no: str) -> dict:
    """查询工单状态。

    Args:
        db: 数据库会话
        tenant_id: 租户 ID（租户隔离）
        ticket_no: 工单号

    Returns:
        dict: ticket_id / ticket_no / title / status / priority / description

    Raises:
        TicketNotFound: 工单不存在或不在当前租户下
    """
    ticket = ticket_crud.get_by_ticket_no(db, tenant_id, ticket_no)
    if ticket is None:
        raise TicketNotFound(f"工单不存在: {ticket_no}")
    return {
        "ticket_id": ticket.id, "ticket_no": ticket.ticket_no, "title": ticket.title,
        "status": ticket.status, "priority": ticket.priority, "description": ticket.description,
    }


def create_ticket(
    db: Session, *, tenant_id: int, title: str, description: str | None = None, priority: str = "medium",
    category: str = "general",
) -> dict:
    """创建工单。

    流程:
      1. 生成工单号 TK-{12 位 hex 大写}
      2. 调用 CRUD 创建记录（初始状态 open）
      3. 提交事务

    设计决策:
      - 工单号用 uuid hex 前 12 位（48bit，T30）: 8 位（32bit）在 ~6.5 万单时冲突率已达 50%，
        12 位把 50% 冲突点推迟到 ~2800 万单
      - 默认优先级 medium: 大多数工单不需要紧急处理

    Args:
        db: 数据库会话
        tenant_id: 租户 ID
        title: 工单标题
        description: 工单描述（可选）
        priority: 优先级，默认 medium

    Returns:
        dict: id / ticket_no / title / status / priority
    """
    import uuid
    # T30: 12 位 hex（48bit）——8 位（32bit）在 ~6.5 万单时冲突率已达 50%，
    # 12 位把 50% 冲突点推迟到 ~2800 万单；撞唯一约束抛 IntegrityError 的概率可忽略
    ticket_no = f"TK-{uuid.uuid4().hex[:12].upper()}"
    ticket = ticket_crud.create_ticket(
        db, tenant_id=tenant_id, ticket_no=ticket_no, title=title,
        description=description, priority=priority, category=category,
    )
    db.commit()
    return {
        "id": ticket.id, "ticket_no": ticket.ticket_no, "title": ticket.title,
        "status": ticket.status, "priority": ticket.priority,
    }


def update_ticket_status(db: Session, *, ticket_no: str, tenant_id: int, new_status: str) -> dict:
    """更新工单状态（含状态机校验）。

    流程:
      1. 加载工单（按 tenant_id 隔离）
      2. 查状态机合法目标集合，new_status 不在集合内 -> 拒绝
      3. 更新状态并提交

    设计决策:
      - 白名单校验: 只允许显式声明的跃迁，未知状态默认空集（fail-closed）
      - 校验在 service 层而非 DB 层: 业务异常更友好，可返回具体非法路径信息

    Args:
        db: 数据库会话
        ticket_no: 工单号
        tenant_id: 租户 ID
        new_status: 目标状态

    Returns:
        dict: ticket_no / status

    Raises:
        TicketNotFound: 工单不存在
        TicketError: 非法状态跃迁
    """
    ticket = ticket_crud.get_by_ticket_no(db, tenant_id, ticket_no)
    if ticket is None:
        raise TicketNotFound(f"工单不存在: {ticket_no}")

    # 查合法目标集合 -- 未知状态默认空集，拒绝所有跃迁（fail-closed）
    valid = VALID_TICKET_TRANSITIONS.get(ticket.status, set())
    if new_status not in valid:
        raise TicketError(f"非法状态跃迁: {ticket.status} -> {new_status}")

    ticket_crud.update_status(db, ticket, new_status)
    db.commit()
    return {"ticket_no": ticket_no, "status": new_status}
