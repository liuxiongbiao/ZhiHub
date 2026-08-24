"""
backend/app/core/rabbitmq.py

职责: 知识处理任务的 Celery/RabbitMQ 发布、租户公平分片，以及旧版直连消费的兼容封装

核心设计:
  API 上传文档后使用 Celery 协议非阻塞投递；队列名由
  `tenant_id % 8` 稳定分片为 knowledge.0..7。生产 knowledge worker 将
  prefetch 固定为 1 并启用 -Ofair，避免单个租户的大批任务独占消费能力。

关键技术点:
  - topic exchange: 按 routing key 模式匹配分发（支持通配符 * 和 #），
    比 direct 更灵活，未来可扩展 knowledge.process.* 等子路由。
  - durable=True: exchange 和 queue 持久化，RabbitMQ 重启后不丢失拓扑定义。
  - delivery_mode=2: 消息持久化到磁盘，RabbitMQ 重启后消息不丢（配合 durable queue）。
  - prefetch_count=1: 每次只取一条消息处理，处理完 ACK 后再取下一条。
    避免 worker 崩溃时多条消息丢失（未 ACK 的消息会重新入队）。
  - NACK requeue=False: 消息处理异常时不重新入队，避免毒消息死循环。
    代价是消息丢失（demo 可接受），生产环境应配死信队列（DLX）兜底。

Queue 设计:
  - 生产: knowledge.0..7（租户分片），knowledge 作为旧任务/默认路由兼容队列
  - 旧版兼容: knowledge_processing + zhihub.topic/knowledge.process（不再由 Web 进程启动消费器）

依赖关系:
  - 上游: app/services/knowledge_service.py（上传/重建后 publish）
  - 下游: celery_worker.py + RabbitMQ + app/tasks/knowledge_tasks.py
"""

import json
from collections.abc import Callable

import pika
from pika.adapters.blocking_connection import BlockingChannel

from app.config import settings

EXCHANGE_NAME = "zhihub.topic"
KNOWLEDGE_QUEUE = "knowledge_processing"
KNOWLEDGE_ROUTING_KEY = "knowledge.process"
KNOWLEDGE_QUEUE_BUCKETS = 8


def knowledge_queue_for_tenant(tenant_id: int) -> str:
    """把租户稳定分散到多个消费队列，避免单一租户的大批文档长期阻塞其他租户。"""
    return f"knowledge.{int(tenant_id) % KNOWLEDGE_QUEUE_BUCKETS}"


def _get_connection() -> pika.BlockingConnection:
    """建立 RabbitMQ 阻塞连接（每次调用新建，使用后需 close）。"""
    params = pika.URLParameters(settings.rabbitmq_url)
    return pika.BlockingConnection(params)


def declare_queues():
    """声明旧版 Exchange/Queue（幂等兼容入口）。

    幂等性: exchange_declare/queue_declare 对已存在的拓扑不报错，
    适合启动时调用确保拓扑就绪。
    """
    conn = _get_connection()
    channel = conn.channel()

    # topic exchange：按 routing key 模式匹配分发（支持 * 和 # 通配符）
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)

    # 知识库处理队列（durable=True 持久化，RabbitMQ 重启后队列定义不丢）
    channel.queue_declare(queue=KNOWLEDGE_QUEUE, durable=True)
    channel.queue_bind(
        exchange=EXCHANGE_NAME,
        queue=KNOWLEDGE_QUEUE,
        routing_key=KNOWLEDGE_ROUTING_KEY,
    )

    conn.close()


def publish_knowledge_task(doc_id: int, tenant_id: int) -> None:
    """通过 Celery 协议投递到租户分片 knowledge 队列。"""
    from celery_worker import celery_app

    celery_app.send_task(
        "app.tasks.knowledge_tasks.process_document",
        args=[doc_id, tenant_id],
        queue=knowledge_queue_for_tenant(tenant_id),
    )


def consume_knowledge_tasks(callback: Callable[[int, int], None]) -> None:
    """消费旧版 knowledge_processing 队列（阻塞式兼容入口）。

    流程: 新建连接 -> 幂等声明 -> QoS prefetch=1 -> 注册回调 -> start_consuming 阻塞。
    callback 签名: callback(doc_id: int, tenant_id: int) -> None
    成功: ACK 确认；异常: NACK requeue=False 丢弃（防毒消息死循环）。

    prefetch_count=1 的意义: 每次只投递一条消息给 worker，
    处理完 ACK 后才投递下一条。worker 崩溃时未 ACK 的消息会重新入队，最多丢失一条。

    注意: 生产已由 Celery knowledge worker 消费分片队列；此函数仅供
    旧部署兼容，不由 Web 进程启动。若使用，必须在独立进程中运行。

    Args:
        callback: 消息处理回调，签名为 callback(doc_id, tenant_id) -> None
    """
    conn = _get_connection()
    channel = conn.channel()

    # 幂等声明 exchange/queue（RabbitMQ 重启后队列可能丢失）
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
    channel.queue_declare(queue=KNOWLEDGE_QUEUE, durable=True)
    channel.queue_bind(exchange=EXCHANGE_NAME, queue=KNOWLEDGE_QUEUE, routing_key=KNOWLEDGE_ROUTING_KEY)

    # 每次只取一条，处理完再取（避免 worker 崩溃时丢失多条消息）
    channel.basic_qos(prefetch_count=1)

    def _on_message(ch: BlockingChannel, method, properties, body):
        try:
            data = json.loads(body)
            doc_id = data["doc_id"]
            tenant_id = data["tenant_id"]
            callback(doc_id, tenant_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            # 解析失败或回调异常 → 拒绝消息且不重新入队（避免毒消息死循环）
            # ⚠️ 潜在问题: requeue=False 会直接丢弃消息，生产环境应配死信队列（DLX）兜底
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    channel.basic_consume(queue=KNOWLEDGE_QUEUE, on_message_callback=_on_message)
    channel.start_consuming()
