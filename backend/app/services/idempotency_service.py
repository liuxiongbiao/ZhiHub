"""
backend/app/services/idempotency_service.py

职责: 业务级幂等编排 - 检查幂等键 -> 已存在返回缓存结果 -> 不存在执行业务 + 记录

核心设计:
  幂等的目的是保证"同一请求执行多次效果等同于一次"。本模块提供通用编排函数：
  先查 client_request_id 是否已有记录，命中则直接返回缓存结果；未命中则用 SETNX 抢占
  in-progress 标记（防 TOCTOU 竞态），再执行业务函数，成功后回写幂等记录。

关键技术点:
  - check-then-lock-then-execute-then-record: 四步编排，SETNX 锁消除 check 与 record 间的竞态窗口
  - SETNX in-progress 标记: 并发请求只有一个能抢占，另一个被拒绝重试（重试时可能命中缓存）
  - 失败清理标记: 业务异常时删除 in-progress 标记，允许客户端重试
  - 失败不记录最终结果: 业务函数抛异常时不回写幂等记录，允许重试

依赖关系:
  - 上游: app/api 层（需要幂等保护的写操作端点）
  - 下游: app.core.idempotency（幂等键存储）、app.core.redis（in-progress 锁）
"""

import logging

from app.core.idempotency import IDEMPOTENCY_TTL, check_idempotency, record_idempotency
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


def with_idempotency(
    client_request_id: str, resource_type: str, action: callable,
    *args, namespace: str | int = "generic", **kwargs,
) -> tuple[dict, bool]:
    """幂等编排: 检查 -> 抢占锁 -> 执行业务 -> 记录。

    流程:
      1. 检查 client_request_id 是否已有记录
      2. 命中 -> 返回 (cached_result, True)，不执行业务函数
      3. 未命中 -> SETNX 抢占 in-progress 标记，抢不到说明并发，抛异常让客户端重试
      4. 执行业务函数 action(*args, **kwargs)
      5. 从结果提取 resource_id（dict 取 "id"，对象取 .id）
      6. 回写幂等记录，清理 in-progress 标记，返回 (result, False)

    设计决策:
      - SETNX in-progress 锁: 解决 check 与 record 间的 TOCTOU 竞态。两个并发请求都 check
        未命中时，SETNX 只有一个成功，另一个被拒绝；被拒绝的客户端重试时若首次已完成则命中缓存。
      - 业务失败清理标记: 异常时删除 in-progress 标记，否则会阻塞同 key 的重试直至 TTL。
      - resource_id 提取兼容 dict 和 ORM 对象: 调用方返回类型灵活。

    Args:
        client_request_id: 客户端幂等键（唯一标识本次逻辑请求）
        resource_type: 资源类型（如 "refund"/"ticket"），用于幂等记录分类
        action: 业务可调用对象，未命中时执行
        *args, **kwargs: 传给 action 的参数
        namespace: 幂等命名空间（退款场景传 order_id，与 core 层 key 粒度一致，
            防跨租户/跨订单同 id 碰撞）

    Returns:
        tuple[dict, bool]: (结果字典, is_cached) -- is_cached=True 表示命中缓存

    Raises:
        RuntimeError: 并发请求正在处理同一 client_request_id（TOCTOU 竞态拦截）
    """
    cached = check_idempotency(namespace, client_request_id)
    if cached:
        # 命中缓存 —— 直接返回，不执行业务函数，拦截重复请求/网络重试
        logger.info(f"Idempotency hit: {client_request_id}")
        return cached, True

    # 抢占 in-progress 标记（SETNX 原子），防 TOCTOU 竞态：
    # 两个并发请求都 check 未命中，此处只有一个能 SETNX 成功，另一个拒绝重试
    r = get_redis()
    processing_key = f"idempotency:processing:{namespace}:{client_request_id}"
    if not r.set(processing_key, "1", ex=IDEMPOTENCY_TTL, nx=True):
        raise RuntimeError("并发请求处理中，请稍后重试")

    try:
        result = action(*args, **kwargs)
        # 从结果提取 resource_id —— 兼容 dict（取 "id"）和 ORM 对象（取 .id）
        resource_id = result["id"] if isinstance(result, dict) else result.id
        record_idempotency(namespace, client_request_id, resource_type, resource_id)
    except Exception:
        # 业务失败：清理 in-progress 标记，允许客户端重试
        r.delete(processing_key)
        raise

    # 业务成功：清理 in-progress 标记（最终结果已由 record_idempotency 写入）
    r.delete(processing_key)

    # 统一返回 dict —— ORM 对象也包装成 {resource_type, resource_id}
    if isinstance(result, dict):
        return result, False
    return {"resource_type": resource_type, "resource_id": resource_id}, False
