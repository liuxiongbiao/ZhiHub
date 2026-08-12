"""
backend/app/services/audit_service.py

职责: 系统审计日志写入与查询（PRD 2.1）

核心设计:
  log_audit 只 add + flush 不 commit，由各 service 的 commit 统一提交，
  保证"操作成功才有审计记录"的原子性 -- 业务操作回滚则审计一并回滚，
  不会出现"操作失败但留下了审计记录"的不一致状态。

  登录/登出无其他 DB 写入，调用方需显式 commit 落审计记录。

关键技术点:
  - 同事务原子性: 审计记录与业务操作共用同一 db session 和事务，
    flush 让记录进入事务缓冲区，commit 时与业务变更一起持久化
  - tenant_id 隔离: 每条记录绑定 tenant_id，查询端点强制 WHERE tenant_id=?
  - target_id 统一转 str: 兼容 int 主键和字符串标识（如 order_no）
  - 不记 PII: detail 字段不含手机号等敏感信息，仅记录操作元数据

依赖关系:
  - 上游: order_service / auth_service / tenant_service / billing_service（埋点调用）
  - 下游: app.models.audit_log.AuditLog（持久化）
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def log_audit(
    db: Session,
    *,
    tenant_id: int,
    user_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    detail: str | None = None,
) -> AuditLog:
    """写入一条审计日志（add + flush，不 commit）。

    设计决策:
      不 commit -- 审计记录与业务操作同事务，由调用方的 commit 统一提交。
      这样保证原子性: 业务回滚 -> 审计回滚，不会留下"操作失败但有审计"的不一致。
      登录/登出等无 DB 写入的场景，调用方需显式 db.commit()。

    Args:
        db: 数据库会话
        tenant_id: 租户 ID（隔离键）
        user_id: 操作人用户 ID（系统操作可传 None）
        action: 操作类型（refund_approve/refund_cancel/login/logout/wizard_advance/payment）
        target_type: 操作对象类型（refund_approval/user/tenant/billing_invoice）
        target_id: 操作对象 ID（自动转 str 兼容 int/非 int）
        detail: 附加描述（不含 PII）

    Returns:
        AuditLog: 已 flush 的审计记录（含自增 id）
    """
    audit = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
    )
    db.add(audit)
    db.flush()
    return audit


def list_audit_logs(
    db: Session, tenant_id: int, *, page: int = 1, page_size: int = 20
) -> tuple[list[AuditLog], int]:
    """分页查询租户审计日志（按创建时间倒序）。

    强制 tenant_id 过滤，租户隔离 -- 跨租户不可见。

    Args:
        db: 数据库会话
        tenant_id: 租户 ID（隔离键）
        page: 页码（从 1 开始）
        page_size: 每页条数

    Returns:
        (items, total): 审计日志列表 + 总数
    """
    total = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id)
    ).scalar() or 0
    items = db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return list(items), total
