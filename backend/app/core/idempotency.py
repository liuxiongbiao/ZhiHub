"""
backend/app/core/idempotency.py

职责: 幂等键 Redis 读写基础封装（SETNX 防重复提交）
分工: 🟢 弱模型可独立完成
对应日程: Day 5

核心设计:
  客户端为每个请求生成唯一 client_request_id，首次请求时服务端用 SETNX 写入 Redis。
  重复请求（相同 client_request_id）命中已有记录，直接返回首次结果，不重复执行副作用。

关键技术点:
  - SETNX（SET if Not eXists）原理: Redis 单线程执行 SET ... NX 是原子操作，
    并发请求只有一个能写入成功，其余返回 nil。这是分布式幂等的基础原语。
  - TTL 24h: 过期后 key 自动删除，视为新请求（无幂等保护）。
    24h 覆盖绝大多数客户端重试窗口；超长重试视为新请求是可接受的。
  - 双重防御: Redis SETNX 防并发重复 + DB unique 约束兜底。
    SETNX 失败（返回 False）说明并发竞争中另一请求已先写入，
    此时 DB 的 unique 约束也会拒绝重复创建，两层防护确保不产生重复资源。

键设计:
  idempotency:{namespace}:{client_request_id} -> JSON {"resource_type": "refund", "resource_id": 5}
  namespace 通常为 order_id（退款场景）：幂等只对"同一订单的同一请求 id"生效，
  跨租户/跨订单使用相同 client_request_id 互不干扰（防跨租户碰撞返回他租户的退款缓存）。
  TTL = 24h，过期后自动清理，过期视为新请求（无幂等保护）

依赖关系:
  - 上游: app/api/* 退款/创建类接口（POST 幂等保护）
  - 下游: app.core.redis.get_redis
"""

import json

from app.core.redis import get_redis

# 幂等键 TTL: 24 小时（覆盖客户端重试窗口，过期视为新请求）
IDEMPOTENCY_TTL = 24 * 60 * 60


def _idempotency_key(namespace: str | int, client_request_id: str) -> str:
    """构造幂等键的 Redis key。

    key 粒度 = (namespace, client_request_id)：退款场景 namespace 传 order_id，
    幂等只对"同一订单的同一请求 id"生效；跨订单/跨租户同 id 互不干扰
    （全局 key 会让租户 B 命中租户 A 的退款缓存，跨租户资金数据泄露）。
    """
    return f"idempotency:{namespace}:{client_request_id}"


def check_idempotency(namespace: str | int, client_request_id: str) -> dict | None:
    """检查幂等键是否已存在（请求是否为重复提交）。

    流程: GET idempotency:{namespace}:{client_request_id} -> 有值则 JSON 解码返回首次结果。
    调用方拿到非 None 结果时应直接返回首次创建的资源，跳过重复执行。

    Args:
        namespace: 幂等命名空间（退款场景传 order_id，防跨租户/跨订单碰撞）
        client_request_id: 客户端生成的唯一请求标识

    Returns:
        已存在的幂等记录 dict（resource_type, resource_id），不存在返回 None
    """
    r = get_redis()
    data = r.get(_idempotency_key(namespace, client_request_id))
    if data:
        return json.loads(data)
    return None


def record_idempotency(
    namespace: str | int, client_request_id: str, resource_type: str, resource_id: int
) -> bool:
    """写入幂等记录（SETNX 原子操作，防并发重复写入）。

    流程: SET key value EX 24h NX -> 仅当 key 不存在时写入。
    设计决策: 用 NX 而非普通 SET，保证并发请求中只有一个能写入成功。
    DB unique 约束作为第二层兜底: 即使 SETNX 竞争失败，DB 也会拒绝重复创建。

    Args:
        namespace: 幂等命名空间（退款场景传 order_id，需与 DB 复合唯一约束粒度一致）
        client_request_id: 客户端生成的唯一请求标识
        resource_type: 资源类型（如 "refund"）
        resource_id: 首次创建的资源 ID

    Returns:
        True = 首次写入成功；False = 键已存在（并发竞争，另一请求已先记录）
    """
    r = get_redis()
    payload = json.dumps({"resource_type": resource_type, "resource_id": resource_id})
    return bool(r.set(_idempotency_key(namespace, client_request_id), payload, ex=IDEMPOTENCY_TTL, nx=True))
