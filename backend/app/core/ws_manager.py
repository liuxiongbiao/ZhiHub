"""
backend/app/core/ws_manager.py

职责: WebSocket 连接管理 -- 按 tenant_id 分组管理坐席工作台长连接，
      支持向某租户所有在线坐席广播实时事件（新转人工会话 / 新消息）。
对应日程: PRD 3.7 -- WebSocket 实时推送坐席工作台

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
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 单连接推送超时（T31）: 慢/卡死客户端不再阻塞同租户全部坐席的实时推送
WS_SEND_TIMEOUT = 5.0


class ConnectionManager:
    """WebSocket 连接管理器: tenant_id -> set[WebSocket]。

    非线程安全（依赖 GIL + asyncio 单循环），单进程单循环场景下足够。
    多 worker 部署时每个 worker 各自一份连接表，广播仅本 worker 的坐席收到；
    如需跨 worker 推送，需额外引入 Redis Pub/Sub（本 demo 单 worker 不做）。
    """

    def __init__(self) -> None:
        # tenant_id -> 该租户所有在线坐席 WS 连接集合
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # WS 端点所在事件循环（connect 时捕获），供同步广播跨线程调度协程
        self._loop: asyncio.AbstractEventLoop | None = None

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

    async def broadcast_to_tenant(self, tenant_id: int, message: dict[str, Any]) -> None:
        """向某租户所有在线坐席 WS 推送消息（异步，须在事件循环中调用）。

        发送失败的连接视为已关闭，从表中移除，避免僵尸连接累积。
        T31: 每个连接 send 加 5s 超时——慢/卡死客户端不再阻塞同租户全部推送
        （head-of-line blocking）。
        """
        conns = list(self._connections.get(tenant_id, set()))
        for ws in conns:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=WS_SEND_TIMEOUT)
            except Exception as e:
                # 连接已关闭 / 客户端断开 / 发送超时 -- 清理
                logger.warning(f"WS send failed, removing connection: {e}")
                self._connections[tenant_id].discard(ws)
                try:
                    await ws.close()  # L9: 显式关闭底层连接，防资源泄漏（客户端不知服务端已放弃推送）
                except Exception:
                    pass  # close 失败可忽略，连接已从表移除

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
        if not self._connections.get(tenant_id):
            # 无在线坐席，无需推送
            return

        loop = self._loop
        if loop is not None and loop.is_running():
            # 业务代码在线程池，WS 在循环线程 -- 跨线程提交协程
            asyncio.run_coroutine_threadsafe(
                self.broadcast_to_tenant(tenant_id, message), loop
            )
        else:
            # 兜底: 无运行循环时新建一个跑（通常无连接，不会实际发送）
            try:
                asyncio.run(self.broadcast_to_tenant(tenant_id, message))
            except RuntimeError:
                # 已有运行循环但未捕获 -- 忽略，避免崩溃
                logger.warning("broadcast_to_tenant_sync: cannot schedule broadcast")


# 模块级单例 -- 全进程共享一份连接表
manager = ConnectionManager()
