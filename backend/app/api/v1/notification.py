"""app/api/v1/notification.py

站内信路由：
  GET /notifications                列表（?unread=1 只看未读）
  GET /notifications/unread-count   未读数
  POST /notifications/{id}/read     标记已读
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db

router = APIRouter()


def _get_tenant_id(request: Request) -> int:
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


@router.get("")
def list_notifications(
    request: Request,
    unread: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from app.services.notification_service import list_notifications as _list
    return _list(
        db, _get_tenant_id(request),
        user_id=getattr(request.state, "user_id", None),
        unread=unread, page=page, page_size=page_size,
    )


@router.get("/unread-count")
def get_unread_count(request: Request, db: Session = Depends(get_db)):
    from app.services.notification_service import unread_count
    return {"count": unread_count(db, _get_tenant_id(request), user_id=getattr(request.state, "user_id", None))}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.notification_service import mark_read as _mark
    ok = _mark(
        db, notification_id, _get_tenant_id(request),
        user_id=getattr(request.state, "user_id", None),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在")
    return {"id": notification_id, "read": True}
