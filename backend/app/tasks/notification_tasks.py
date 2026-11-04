"""
backend/app/tasks/notification_tasks.py

职责: Mock 通知 - 配额预警/转人工提醒的站内信通知（demo 用日志 + Redis 计数占位）

核心设计:
  生产环境应接 WebSocket 实时推送 + 短信/邮件网关。

关键技术点:
  - Redis 计数占位: 用 INCR 累计通知次数，前端可轮询展示通知量（模拟可观测性）。
    计数 key 带 7 天 TTL，过期自动清理，避免永久累积。
  - 配额预警: 用量达 80% / 100% 时触发，提醒租户升级或限制使用。
  - 转人工通知: 会话转接人工时通知坐席工作台，demo 靠轮询感知。

简化说明:
  真实产品会接 WebSocket 推送/短信/邮件，demo 阶段写日志 + Redis 计数即可，

依赖关系:
  - 上游: app/services/billing_service（配额预警）、app/services/session_service（转人工）
  - 下游: Redis（计数占位）
"""

import logging

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

NOTIF_COUNT_PREFIX = "notif:count:"
# 计数 key TTL: 7 天，过期自动清理，避免永久累积无用 key
NOTIF_TTL = 7 * 24 * 3600


def _write_quota_notification(tenant_id: int, level: str, count: int, quota: int) -> None:
    try:
        from app.core.database import SessionLocal
        from app.services.notification_service import create_notification

        db = SessionLocal()
        try:
            create_notification(
                db, tenant_id=tenant_id,
                title=f"配额预警：{level}",
                content=f"当前用量 {count}/{quota}，已触发 {level} 预警，超出部分将按量计费。",
                type="quota_warning",
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"Quota warning notification write failed (non-blocking): {exc}")


def _quota_admin_emails(tenant_id: int) -> list:
    try:
        from app.core.database import SessionLocal
        from app.models.tenant_member import TenantMember
        from app.models.user import User
        from sqlalchemy import select

        db = SessionLocal()
        try:
            return [
                email for email in db.execute(
                    select(User.email)
                    .join(TenantMember, TenantMember.user_id == User.id)
                    .where(
                        TenantMember.tenant_id == tenant_id,
                        TenantMember.role == "admin",
                        User.email.isnot(None),
                    )
                ).scalars().all() if email
            ]
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"Quota warning admin emails load failed: {exc}")
        return []


def _send_quota_emails(emails: list, tenant_id: int, level: str, count: int, quota: int) -> None:
    from app.core.email_client import send_email

    for email in emails:
        try:
            send_email(
                to=email,
                subject=f"[智枢] 配额预警：{level}",
                text=f"租户 {tenant_id} 当月对话用量已达 {level}（{count}/{quota}），超出部分将按量计费。请及时关注或升级套餐。",
            )
        except Exception as exc:
            logger.warning(f"Quota warning email failed (to={email}): {exc}")


def notify_quota_warning(tenant_id: int, level: str, count: int, quota: int) -> None:
    """配额预警通知（80%/100%）。

    Demo 实现: 写 WARNING 日志 + Redis INCR 计数。
    生产环境: 应推送站内信/邮件/短信给租户管理员。

    Args:
        tenant_id: 租户 ID
        level: 预警级别 "80%" 或 "100%"
        count: 当前用量
        quota: 配额上限
    """
    message = f"[配额预警] 租户 {tenant_id} 用量已达 {level}（{count}/{quota}）"
    logger.warning(message)
    _write_quota_notification(tenant_id, level, count, quota)
    _send_quota_emails(_quota_admin_emails(tenant_id), tenant_id, level, count, quota)

    r = get_redis()
    key = f"{NOTIF_COUNT_PREFIX}quota_warning:{tenant_id}"
    r.incr(key)
    r.expire(key, NOTIF_TTL)


# 已移除（全项目无调用方）: notify_human_handoff / notify_agent_claim 全项目无调用方（转人工/认领推送
# 实际由 human_service 直接走 ws_manager 完成）；保留 notify_quota_warning（有调用方）。
