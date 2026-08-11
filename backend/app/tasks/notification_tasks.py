"""
backend/app/tasks/notification_tasks.py

职责: Mock 通知 - 配额预警/转人工提醒的站内信通知（demo 用日志 + Redis 计数占位）
分工: 🟢 弱模型可独立完成
对应日程: Day 6

核心设计:
  Demo 阶段通知只写日志 + Redis 计数（可观测性演示），不接真实推送通道。
  生产环境应接 WebSocket 实时推送 + 短信/邮件网关。

关键技术点:
  - Redis 计数占位: 用 INCR 累计通知次数，前端可轮询展示通知量（模拟可观测性）。
    计数 key 带 7 天 TTL，过期自动清理，避免永久累积。
  - 配额预警: 用量达 80% / 100% 时触发，提醒租户升级或限制使用。
  - 转人工通知: 会话转接人工时通知坐席工作台，demo 靠轮询感知。

简化说明:
  真实产品会接 WebSocket 推送/短信/邮件，demo 阶段写日志 + Redis 计数即可，
  Day 10 演示时口头说明"生产环境接 WebSocket + 短信网关"。

依赖关系:
  - 上游: app/services/billing_service（配额预警）、app/services/session_service（转人工）
  - 下游: Redis（计数占位）
"""

import logging

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Redis 通知计数键前缀（仅用于演示可观测性）
NOTIF_COUNT_PREFIX = "notif:count:"
# 计数 key TTL: 7 天，过期自动清理，避免永久累积无用 key
NOTIF_TTL = 7 * 24 * 3600


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

    # 记录通知计数（可观测性演示），带 TTL 防永久累积
    r = get_redis()
    key = f"{NOTIF_COUNT_PREFIX}quota_warning:{tenant_id}"
    r.incr(key)
    r.expire(key, NOTIF_TTL)


# T29 移除: notify_human_handoff / notify_agent_claim 全项目无调用方（转人工/认领推送
# 实际由 human_service 直接走 ws_manager 完成）；保留 notify_quota_warning（有调用方）。
