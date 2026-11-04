"""
backend/app/core/ws_manager.py

职责: WebSocket 连接管理 -- 按 tenant_id 分组管理坐席工作台长连接，
      支持向某租户所有在线坐席广播实时事件（新转人工会话 / 新消息）。

核心设计:
  - 多租户隔离: 连接按 tenant_id 分桶到 set[WebSocket]，广播时只推该租户的坐席，
    严禁跨租户泄漏（宪法原则: 租户隔离）。
  - 单例模式: 模块级 `manager` 单例，全进程共享一份连接表，避免多实例连接表分裂。
  - 同步触发异步推送: 业务代码（agent_service 等同步函数）调用 `broadcast_to_tenant_sync`
    触发推送；内部用 run_coroutine_threadsafe 把协程调度到 WS 所在事件循环上执行。
    原因: WebSocket.send 必须在其所属事件循环中调用，而同步业务函数运行在 anyio 线程池，
    不在循环线程上。run_coroutine_threadsafe 是跨线程提交协程到指定循环的标准做法。

关键技术点:
  - run_coroutine_threadsafe vs asyncio.run: 后者会新建循环，WS 连接属于原循环，
    新循环里 send 会报 "different event loop"。前者把任务提交到 WS 循环，安全。
  - 连接时捕获 loop: WS 端点在事件循环中执行，connect 时 asyncio.get_running_loop()
    拿到循环并缓存，供后续同步广播使用。
  - 发送失败即踢连接: WebSocket 关闭/网络异常时 send 抛异常，捕获后从连接表移除，
    避免向僵尸连接反复发送。

依赖关系:
  - 上游: app.api.v1.ws（WS 端点调 connect/disconnect）、
          app.services.agent_service（转人工时调 broadcast_to_tenant_sync）
  - 下游: fastapi.WebSocket
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from redis import asyncio as async_redis

from app.config import settings

logger = logging.getLogger(__name__)

# 单连接推送超时: 慢/卡死客户端不再阻塞同租户全部坐席的实时推送
WS_SEND_TIMEOUT = 5.0


class ConnectionManager:
    """WebSocket 连接管理器: tenant_id -> set[WebSocket]。

    非线程安全（依赖 GIL + asyncio 单循环），单进程单循环场景下足够。
    多 worker 部署时每个 worker 各自一份连接表，广播仅本 worker 的坐席收到；
    如需跨 worker 推送，需额外引入 Redis Pub/Sub（本 demo 单 worker 不做）。
    """

    def __init__(self, channel: str | None = None) -> None:
        # tenant_id → 该租户所有在线坐席 WS 连接集合
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # session_id → 该会话终端用户连接；客服消息只推送目标会话，避免租户内串话。
        self._session_connections: dict[int, set[WebSocket]] = defaultdict(set)
        # WS 端点所在事件循环（connect 时捕获），供同步广播跨线程调度协程
        self._loop: asyncio.AbstractEventLoop | None = None
        self._channel = channel or settings.ws_pubsub_channel
        self._redis_async: Any | None = None
        self._pubsub: Any | None = None
        self._listener_task: asyncio.Task | None = None
        self._listener_stop: asyncio.Event | None = None
        self._owns_redis = False
        self._pubsub_started = False

    async def start_pubsub(self, redis_client: Any | None = None) -> None:
        """订阅跨进程广播通道；每个 API worker 只投递自己的本地连接。"""
        if self._listener_task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._owns_redis = redis_client is None
        self._redis_async = redis_client or async_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        self._pubsub = self._redis_async.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._pubsub_started = True
        self._listener_stop = asyncio.Event()
        self._listener_task = asyncio.create_task(
            self._listen_pubsub(), name="zhihub-ws-pubsub",
        )

    async def stop_pubsub(self) -> None:
        """停止订阅并释放当前 worker 的 Pub/Sub 连接。"""
        if self._listener_stop is not None:
            self._listener_stop.set()
        task, self._listener_task = self._listener_task, None
        self._pubsub_started = False
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._listener_stop = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(self._channel)
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None
        if self._owns_redis and self._redis_async is not None:
            with contextlib.suppress(Exception):
                await self._redis_async.aclose()
        self._redis_async = None

    async def _listen_pubsub(self) -> None:
        """消费 Redis 事件并向当前进程持有的连接发送。

        不用 PubSub.listen()：它内部以无限超时阻塞，Windows 上取消任务时
        asyncio.wait_for 可能吞掉 CancelledError，监听协程就再也退不出来。
        get_message 有超时，循环同时看停止事件，关闭时能返回。
        """
        assert self._pubsub is not None
        while self._listener_stop is None or not self._listener_stop.is_set():
            try:
                item = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.05,
                )
            except asyncio.CancelledError:
                break
            if self._listener_stop is not None and self._listener_stop.is_set():
                break
            if not item or item.get("type") != "message":
                continue
            try:
                event = json.loads(item["data"])
                target_id = int(event["target_id"])
                if event["target"] == "tenant":
                    await self._broadcast_local_tenant(target_id, event["message"])
                elif event["target"] == "session":
                    await self._broadcast_local_session(target_id, event["message"])
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Invalid WS pub/sub event ignored: {exc}")

    async def _publish(self, target: str, target_id: int, message: dict[str, Any]) -> None:
        """发布跨进程事件；未启动订阅时降级为当前进程投递。"""
        if not self._pubsub_started or self._redis_async is None:
            if target == "tenant":
                await self._broadcast_local_tenant(target_id, message)
            else:
                await self._broadcast_local_session(target_id, message)
            return
        await self._redis_async.publish(
            self._channel,
            json.dumps(
                {"target": target, "target_id": target_id, "message": message},
                ensure_ascii=False,
            ),
        )

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """缓存 WS 所在事件循环。connect 时调用。"""
        self._loop = loop

    async def connect(self, websocket: WebSocket, tenant_id: int) -> None:
        """接受 WS 握手并注册到 tenant 分桶。

        先 accept 再入表: accept 失败（如鉴权拒绝前已 close）不入表，避免脏连接。
        """
        await websocket.accept()
        self._connections[tenant_id].add(websocket)
        # 捕获当前事件循环，供同步广播路径使用
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                # 无运行循环（理论上不会发生，WS 端点本身在循环中）
                pass
        logger.info(f"WS connected: tenant={tenant_id}, total={len(self._connections[tenant_id])}")

    def disconnect(self, websocket: WebSocket, tenant_id: int) -> None:
        """移除连接（客户端关闭或服务端踢出时调用）。"""
        self._connections[tenant_id].discard(websocket)
        if not self._connections[tenant_id]:
            self._connections.pop(tenant_id, None)
        logger.info(f"WS disconnected: tenant={tenant_id}")

    async def connect_session(self, websocket: WebSocket, session_id: int) -> None:
        """接受终端用户 WS 并注册到会话分桶。"""
        await websocket.accept()
        self._session_connections[session_id].add(websocket)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

    def disconnect_session(self, websocket: WebSocket, session_id: int) -> None:
        self._session_connections[session_id].discard(websocket)
        if not self._session_connections[session_id]:
            self._session_connections.pop(session_id, None)

    @staticmethod
    def _wire_envelope(message: dict[str, Any]) -> dict[str, Any]:
        """统一输出 type/payload；平铺字段仅作为旧客户端过渡兼容。"""
        message_type = message.get("type") or message.get("event")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            payload = {
                key: value
                for key, value in message.items()
                if key not in {"type", "event", "payload"}
            }
        envelope: dict[str, Any] = {"type": message_type, "payload": payload}
        # 已有测试和灰度客户端仍读取 event/平铺字段；新客户端只消费 envelope。
        envelope["event"] = message_type
        envelope.update(payload)
        return envelope

    async def _broadcast(self, connections: set[WebSocket], message: dict[str, Any]) -> None:
        wire_message = self._wire_envelope(message)
        for websocket in list(connections):
            try:
                await asyncio.wait_for(
                    websocket.send_json(wire_message),
                    timeout=WS_SEND_TIMEOUT,
                )
            except Exception as exc:
                logger.warning(f"WS send failed, removing connection: {exc}")
                connections.discard(websocket)
                try:
                    await websocket.close()
                except Exception:
                    pass

    async def _broadcast_local_tenant(self, tenant_id: int, message: dict[str, Any]) -> None:
        await self._broadcast(self._connections.get(tenant_id, set()), message)

    async def _broadcast_local_session(self, session_id: int, message: dict[str, Any]) -> None:
        await self._broadcast(self._session_connections.get(session_id, set()), message)

    async def broadcast_to_tenant(self, tenant_id: int, message: dict[str, Any]) -> None:
        """向某租户所有在线坐席 WS 推送消息（异步，须在事件循环中调用）。

        发送失败的连接视为已关闭，从表中移除，避免僵尸连接累积。
        : 每个连接 send 加 5s 超时——慢/卡死客户端不再阻塞同租户全部推送
        （head-of-line blocking）。
        """
        await self._publish("tenant", tenant_id, message)

    async def broadcast_to_session(self, session_id: int, message: dict[str, Any]) -> None:
        """向单个会话的终端用户连接实时推送。"""
        await self._publish("session", session_id, message)

    def broadcast_to_tenant_sync(self, tenant_id: int, message: dict[str, Any]) -> None:
        """同步触发广播（供同步业务代码调用）。

        策略:
          1. 若该租户无在线坐席 -> 直接返回，省一次跨线程调度
          2. 若有捕获的事件循环且正在运行 -> run_coroutine_threadsafe 提交到该循环
             （业务代码运行在 anyio 线程池，与 WS 循环不同线程）
          3. 否则（无循环或未运行）-> asyncio.run 新建循环跑一次
             （测试或无 WS 连接时的兜底，不会真正发送因为没有连接）

        注意: 本函数不阻塞等待发送完成（fire-and-forget），调用方无法获知推送结果。
        这是设计取舍: 实时推送是 best-effort，不应阻塞业务主流程。
        """
        if not self._pubsub_started and not self._connections.get(tenant_id):
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            # 业务代码在线程池，WS 在循环线程 —— 跨线程提交协程
            asyncio.run_coroutine_threadsafe(
                self.broadcast_to_tenant(tenant_id, message), loop
            )
        else:
            # 兜底: 无运行循环时新建一个跑（通常无连接，不会实际发送）
            try:
                asyncio.run(self.broadcast_to_tenant(tenant_id, message))
            except RuntimeError:
                # 已有运行循环但未捕获 —— 忽略，避免崩溃
                logger.warning("broadcast_to_tenant_sync: cannot schedule broadcast")

    def broadcast_to_session_sync(self, session_id: int, message: dict[str, Any]) -> None:
        """同步触发目标会话广播，供客服回复服务调用。"""
        if not self._pubsub_started and not self._session_connections.get(session_id):
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.broadcast_to_session(session_id, message), loop,
            )
            return
        try:
            asyncio.run(self.broadcast_to_session(session_id, message))
        except RuntimeError:
            logger.warning("broadcast_to_session_sync: cannot schedule broadcast")


# 模块级单例 —— 全进程共享一份连接表
manager = ConnectionManager()
