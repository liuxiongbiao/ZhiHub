"""
backend/app/crud/ticket.py

职责: tickets 表的数据库访问层（工单 CRUD、按工单号查询、状态更新）
对应日程: 004-ticket-order

核心设计:
  所有查询强制 tenant_id 过滤（多租户隔离）；只 flush 不 commit，事务由 Service 层控制。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ticket import Ticket


def get_by_ticket_no(db: Session, tenant_id: int, ticket_no: str) -> Ticket | None:
    """按 tenant_id + ticket_no 查询工单（双因子校验入口）。"""
    return db.execute(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.ticket_no == ticket_no)
    ).scalar_one_or_none()


def get_by_id(db: Session, ticket_id: int, tenant_id: int) -> Ticket | None:
    """按 tenant_id + 工单主键查询。"""
    return db.execute(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.tenant_id == tenant_id)
    ).scalar_one_or_none()


def list_tickets(
    db: Session, tenant_id: int, *, status: str | None = None, page: int = 1, page_size: int = 20,
) -> tuple[list[Ticket], int]:
    """分页查询工单列表，可选按 status 筛选。返回 (items, total)。"""
    query = select(Ticket).where(Ticket.tenant_id == tenant_id)
    if status:
        query = query.where(Ticket.status == status)
    count_query = select(func.count()).select_from(Ticket).where(Ticket.tenant_id == tenant_id)
    if status:
        count_query = count_query.where(Ticket.status == status)
    total = db.execute(count_query).scalar() or 0
    items = db.execute(
        query.order_by(Ticket.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(items), total


def create_ticket(
    db: Session, *, tenant_id: int, ticket_no: str, title: str,
    description: str | None = None, priority: str = "medium", category: str = "general",
) -> Ticket:
    """创建工单（含工单号、标题、优先级、分类）。"""
    ticket = Ticket(tenant_id=tenant_id, ticket_no=ticket_no, title=title, description=description, priority=priority, category=category)
    db.add(ticket)
    db.flush()
    return ticket


def update_status(db: Session, ticket: Ticket, status: str) -> None:
    """更新工单状态。"""
    ticket.status = status
    db.flush()
