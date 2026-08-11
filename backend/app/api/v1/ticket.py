"""
backend/app/api/v1/ticket.py

职责: 工单列表、详情查询
对应日程: Day 5

端点概览:
  GET /list           工单列表（分页 + 状态筛选）-> ticket_crud.list_tickets
  GET /{ticket_id}    工单详情                    -> ticket_crud.get_by_id

权限: 需登录（tenant_id 隔离，仅查本租户工单）

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/ticket
  - 下游: app.crud.ticket
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import ticket as ticket_crud

router = APIRouter()


def _get_tenant_id(request: Request) -> int:
    """从 request.state 提取 tenant_id，未认证 -> 401。"""
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


class CreateTicketRequest(BaseModel):
    """用户提交工单请求（closure-plan P1-4，PRD 3.8）。"""
    title: str
    description: str | None = None
    priority: str = "medium"
    category: str = "general"


@router.post("", status_code=201)
def create_ticket(body: CreateTicketRequest, request: Request, db: Session = Depends(get_db)):
    """用户主动提交工单（登录用户即可，category 默认 general）。"""
    from app.services.ticket_service import create_ticket as do_create
    return do_create(
        db, tenant_id=_get_tenant_id(request), title=body.title,
        description=body.description, priority=body.priority, category=body.category,
    )


@router.get("/list")
def list_tickets(
    request: Request, status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """工单列表（分页 + 状态筛选）: 调 ticket_crud.list_tickets。"""
    tenant_id = _get_tenant_id(request)
    items, total = ticket_crud.list_tickets(db, tenant_id, status=status_filter, page=page, page_size=page_size)
    return {
        "items": [{"id": t.id, "ticket_no": t.ticket_no, "title": t.title, "status": t.status, "priority": t.priority, "created_at": t.created_at} for t in items],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/{ticket_id}")
def get_ticket_detail(ticket_id: int, request: Request, db: Session = Depends(get_db)):
    """工单详情: 调 ticket_crud.get_by_id，不存在 -> 404。"""
    tenant_id = _get_tenant_id(request)
    ticket = ticket_crud.get_by_id(db, ticket_id, tenant_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"id": ticket.id, "ticket_no": ticket.ticket_no, "title": ticket.title, "description": ticket.description, "status": ticket.status, "priority": ticket.priority, "created_at": ticket.created_at}
