"""
backend/app/services/rate_limit_service.py

职责: 租户级 + 用户级双维度令牌桶限流，以及终端用户对话消息限流

核心设计:
  用 Redis Sorted Set (ZSET) 实现滑动窗口限流：每个请求的时间戳作为 ZSET member，
  score 也是时间戳。每次检查时先清理窗口外的过期 member，再统计窗口内数量是否超限。
  双维度并行：租户 100req/min + 用户 20req/min，任意维度超限即拒绝。

关键技术点:
  - ZSET 滑动窗口: zremrangebyscore 清过期 + zcard 计数，比固定窗口更平滑
  - 双维度并行: 租户级防滥用 + 用户级防单点刷量，两个 ZSET 独立计数
  - member 唯一性: 同一秒多次请求需不同 member，否则 ZSET 去重导致少计
  - TTL 兜底: 窗口 +10s 过期，避免冷键常驻内存

依赖关系:
  - 上游: app/api 中间件/依赖（check_rate_limit）、session 消息端点（check_message_rate_limit）
  - 下游: app.core.redis（ZSET 操作）

阈值: 租户 100req/min, 用户 20req/min
实现: Redis 滑动窗口（key = rate:{tenant_id}:minute / rate:{user_id}:minute）
"""

import time
from app.core.redis import get_redis

import os

# 默认按 PRD 验收阈值；测试环境可用环境变量放宽（如 ZHIHUB_USER_RATE_PER_MIN=500）
TENANT_RATE = int(os.environ.get("ZHIHUB_TENANT_RATE_PER_MIN", "100"))   # 租户每分钟请求数
USER_RATE = int(os.environ.get("ZHIHUB_USER_RATE_PER_MIN", "20"))       # 用户每分钟请求数
WINDOW_SECONDS = 60

# 007 US4: 终端用户对话消息限流（加严，作用于 /session/message 端点）
MESSAGE_RATE = 10   # 单用户每分钟对话消息数
MESSAGE_WINDOW_SECONDS = 60


def check_rate_limit(tenant_id: int, user_id: int) -> dict:
    """双维度限流检查。

    返回: {"allowed": bool, "tenant_count": int, "user_count": int}
    未超限 -> allowed=True; 任意维度超限 -> allowed=False

    流程:
      1. 计算滑动窗口起点 = now - 60s
      2. 租户维度: 清理窗口外过期 member，统计窗口内计数
      3. 用户维度: 同上
      4. 两个维度都未超限 -> 记录本次请求到两个 ZSET
      5. 设 TTL 兜底过期

    设计决策:
      - 先清后计: zremrangebyscore 清窗口外，再 zcard 数窗口内，保证计数准确
      - 只在 allowed 时写入: 被拒绝的请求不进 ZSET，避免拒绝的请求也消耗配额
      - 双维度独立 ZSET: 租户和用户分别计数，互不影响

    Args:
        tenant_id: 租户 ID
        user_id: 用户 ID

    Returns:
        dict: allowed / tenant_count / user_count
    """
    r = get_redis()
    now = int(time.time())
    window_start = now - WINDOW_SECONDS

    # 租户维度
    tenant_key = f"rate:tenant:{tenant_id}:minute"
    r.zremrangebyscore(tenant_key, 0, window_start)  # 清理过期 -- 移除窗口外的旧 member
    tenant_count = r.zcard(tenant_key)

    # 用户维度
    user_key = f"rate:user:{user_id}:minute"
    r.zremrangebyscore(user_key, 0, window_start)
    user_count = r.zcard(user_key)

    allowed = tenant_count < TENANT_RATE and user_count < USER_RATE

    if allowed:
        # 记录本次请求
        ns = time.time_ns()
        r.zadd(tenant_key, {str(ns): now})
        r.zadd(user_key, {str(ns): now})
        r.expire(tenant_key, WINDOW_SECONDS + 10)
        r.expire(user_key, WINDOW_SECONDS + 10)

    return {"allowed": allowed, "tenant_count": tenant_count, "user_count": user_count}


def check_message_rate_limit(user_id: int) -> dict:
    """终端用户对话消息限流（007 US4）：单用户 10 次/分钟，滑动窗口 ZSET。

    作用于 /session/message 端点层，超限 -> 端点返回 429（不落库、不计轮次）。
    与现有 check_rate_limit（租户/用户全局限流）并存，键独立 rate:msg:{user_id}:minute。

    流程:
      1. 清理窗口外过期 member
      2. 统计窗口内计数
      3. 未超限 -> 记录本次请求（member 用 time_ns 保证唯一）

    设计决策:
      - 独立键 rate:msg: 与全局限流分离，消息限流不影响其他 API 调用
      - member 用 time_ns: 纳秒级时间戳保证唯一，同一秒多次调用不会互相覆盖
      - 超限不落库: 429 拒绝的请求不进入会话轮次，不消耗配额

    Args:
        user_id: 用户 ID

    Returns:
        dict: allowed / count
    """
    r = get_redis()
    now = int(time.time())
    window_start = now - MESSAGE_WINDOW_SECONDS
    key = f"rate:msg:{user_id}:minute"

    r.zremrangebyscore(key, 0, window_start)  # 清理过期
    count = r.zcard(key)
    allowed = count < MESSAGE_RATE
    if allowed:
        # member 用 time_ns 保证唯一（同一秒多次调用不会互相覆盖）
        r.zadd(key, {str(time.time_ns()): now})
        r.expire(key, MESSAGE_WINDOW_SECONDS + 10)
    return {"allowed": allowed, "count": count}


def check_widget_rate_limit(session_id: int) -> dict:
    """014 Widget 匿名会话消息限流：单会话 10 次/分钟（与员工消息限流同阈值）。"""
    r = get_redis()
    now = int(time.time())
    window_start = now - MESSAGE_WINDOW_SECONDS
    key = f"rate:widget:{session_id}:minute"

    r.zremrangebyscore(key, 0, window_start)
    count = r.zcard(key)
    allowed = count < MESSAGE_RATE
    if allowed:
        r.zadd(key, {str(time.time_ns()): now})
        r.expire(key, MESSAGE_WINDOW_SECONDS + 10)
    return {"allowed": allowed, "count": count}
