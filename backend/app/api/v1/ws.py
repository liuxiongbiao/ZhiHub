"""
backend/app/api/v1/ws.py

职责: WebSocket 坐席工作台端点 -- 实时推送转人工/认领事件给在线坐席

核心设计:
  - 鉴权用 JWT query 参数 (?token=xxx): 浏览器原生 WebSocket API 不支持自定义请求头，
    只能用 query 参数或子协议传递 token。query 参数是业界通用做法（wss:// 下 TLS
    加密保护 token 不被中间人窃取；注意 token 可能出现在服务端 access log，需脱敏）。
  - 角色限制: 仅 agent/admin 可连。end_user 无需连接工作台，连接即拒。
  - 多租户隔离: 连接按 token 中的 tenant_id 分桶，广播只推同租户坐席。
  - 连接保持: accept 后进入 receive_text 循环保持连接存活，客户端断开抛
    WebSocketDisconnect，捕获后从连接表移除。

关键技术点:
  - WS 不走 HTTP 中间件链: Auth/Tenant 中间件只处理 HTTP 请求，WS 连接需在端点内
    自行解码 JWT + 校验角色，不能依赖 request.state.token_tenant_id。
  - 鉴权失败用 close(code=1008 Policy Violation): WS 握手已升级为 WS 协议，
    无法返回 HTTP 401；用 1008 状态码表示策略拒绝（Starlette 在 accept 前调 close
    会发送 close 帧，客户端收到断开）。
  - connect 时捕获事件循环: ws_manager.connect 内部 get_running_loop 缓存循环，
    供同步业务代码（human_service.claim_session）通过 run_coroutine_threadsafe
    跨线程提交广播协程。

依赖关系:
  - 上游: app.main（挂载 prefix=/api/v1/ws）
  - 下游: app.core.ws_manager（连接管理）、app.core.security（JWT 解码）
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.redis import is_blocklisted
from app.core.security import decode_access_token
from app.core.ws_manager import manager

router = APIRouter()
logger = logging.getLogger(__name__)

# 允许连接工作台的角色 —— end_user 无权连接
_ALLOWED_ROLES = {"agent", "admin"}


@router.websocket("/agent")
async def agent_workstation(websocket: WebSocket) -> None:
    """坐席工作台 WS 端点。

    流程:
      1. 从 query 参数取 token，解码 JWT 校验签名/过期
      2. 校验 role in (agent, admin)，否则 close 1008 拒绝
      3. manager.connect 注册到 tenant 分桶
      4. 进入 receive_text 循环保持连接，客户端断开时清理连接表

    设计决策:
      - 鉴权失败立即 close 不 accept: 避免无效连接进入连接表占用资源
      - 保持循环用 receive_text 而非 sleep: 同时接收客户端心跳/消息，
        客户端主动断开会抛 WebSocketDisconnect，及时感知
      - 不查 JWT 黑名单(jti): 与 HTTP 中间件职责分离一致，黑名单在中间件层；
        WS token 一般是坐席长会话，吊销靠短 exp + 主动断开
    """
    token = websocket.query_params.get("token")

    # 1. token 存在性 + JWT 签名/过期校验
    if not token:
        await websocket.close(code=1008)  # Policy Violation
        return
    payload = decode_access_token(token)
    if payload is None:
        await websocket.close(code=1008)
        return
    # 查 JWT 黑名单（jti）：登出即时失效，与 HTTP 中间件一致
    jti = payload.get("jti")
    if jti:
        try:
            blocked = is_blocklisted(jti)
        except Exception as e:
            # Redis 故障与 HTTP 中间件一致 fail-open
            logger.warning(f"Redis unavailable, WS blocklist check skipped: {e}")
            blocked = False
        if blocked:
            await websocket.close(code=1008)
            return

    # 2. 角色校验 —— 仅坐席/管理员可连工作台
    role = payload.get("role")
    if role not in _ALLOWED_ROLES:
        await websocket.close(code=1008)
        return

    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        await websocket.close(code=1008)
        return

    # 仅接受 /ws-token 签发的短效工作台令牌
    if not payload.get("ws"):
        await websocket.close(code=1008)
        return

    # 3. 注册连接 → accept + 入 tenant 分桶 + 捕获事件循环
    user_id = int(payload["sub"])
    await manager.connect(websocket, tenant_id)
    from app.services.session_service import mark_agent_offline, mark_agent_online
    mark_agent_online(tenant_id, user_id)
    try:
        # 4. 保持连接: 循环接收客户端消息（心跳/指令），断开时抛 WebSocketDisconnect
        # 解析 typing 事件并广播给同租户坐席（输入状态实时同步）
        while True:
            data = await websocket.receive_text()
            await _handle_client_message(data, tenant_id, user_id)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, tenant_id)
        mark_agent_offline(tenant_id, user_id)


async def _handle_client_message(
    data: str,
    tenant_id: int,
    user_id: int | None = None,
    session_id: int | None = None,
) -> None:
    """解析客户端 WS 消息，识别 typing 事件并广播给同租户坐席。

    消息格式: {"type": "typing", "session_id": 123, "is_typing": true}
    非 JSON 或非 typing 类型视为心跳，静默忽略（保持连接存活）。
    """
    try:
        msg = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return  # 非 JSON（纯心跳文本），忽略

    if not isinstance(msg, dict):
        return

    if msg.get("type") == "heartbeat":
        if user_id is not None:
            from app.services.session_service import mark_agent_online
            mark_agent_online(tenant_id, user_id)
        return

    if msg.get("type") == "typing":
        resolved_session_id = session_id if session_id is not None else msg.get("session_id")
        # 广播输入状态给同租户坐席工作台
        await manager.broadcast_to_tenant(tenant_id, {
            "type": "typing",
            "payload": {
                "session_id": resolved_session_id,
                "is_typing": bool(msg.get("is_typing", False)),
            },
            # 旧版调用方兼容；ConnectionManager 会统一输出 envelope。
            "session_id": resolved_session_id,
            "is_typing": bool(msg.get("is_typing", False)),
        })


@router.websocket("/session/{session_id}")
async def end_user_session(websocket: WebSocket, session_id: int) -> None:
    """终端用户会话 WS：仅接收目标会话的人工客服回复。"""
    token = websocket.query_params.get("token")
    payload = decode_access_token(token) if token else None
    if payload is None:
        await websocket.close(code=1008)
        return
    jti = payload.get("jti")
    if jti:
        try:
            if is_blocklisted(jti):
                await websocket.close(code=1008)
                return
        except Exception as exc:
            logger.warning(f"Redis unavailable, WS blocklist check skipped: {exc}")

    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        await websocket.close(code=1008)
        return
    if payload.get("role") == "widget" and payload.get("session_id") != session_id:
        await websocket.close(code=1008)
        return

    from app.core.database import SessionLocal
    from app.crud import session as session_crud
    db = SessionLocal()
    try:
        if session_crud.get_by_id(db, session_id, int(tenant_id)) is None:
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    await manager.connect_session(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Widget/终端用户只能广播自己当前会话的输入状态，session_id 由
            # 已校验的 URL 强制覆盖，不能通过消息体伪造同租户其他会话。
            await _handle_client_message(
                data,
                int(tenant_id),
                session_id=session_id,
            )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_session(websocket, session_id)
