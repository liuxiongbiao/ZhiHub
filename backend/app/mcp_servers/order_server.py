"""
backend/app/mcp_servers/order_server.py

职责: 订单域 MCP Server--暴露 query_order/request_refund 两个工具，按 JSON-RPC 2.0 协议
      定义 inputSchema，分发执行订单业务逻辑，强制 tenant_id 过滤。

核心设计:
  与 ticket_server 同构：定义工具（ORDER_TOOLS）+ 分发执行（handle_order_tool）。
  query_order 走双因子校验（同工单），request_refund 走 order_service.request_refund
  内置的金额上限校验 + client_request_id 幂等。退款是写操作且涉及资金，幂等键防重放是关键。

关键技术点:
  - 双因子校验: query_order 与工单一致，phone 后 4 位匹配订单绑定手机号
  - 退款幂等: client_request_id 作为幂等键，相同键重复请求返回首次结果而非重复退款
  - 金额上限: order_service 内部校验退款金额 <= paid_amount，server 层不重复校验
  - 返回值脱敏: 订单详情不含 phone

依赖关系:
  - 上游: app/services/mcp_service.py（call_tool 分发到 handle_order_tool）
  - 下游: app/mcp_servers/base_server.py（双因子工具）、app/services/order_service.py、app/crud/order.py
"""

import logging

from sqlalchemy.orm import Session

from app.mcp_servers.base_server import IdentityVerificationError, verify_phone_suffix

logger = logging.getLogger(__name__)

# MCP Tool 定义。query_order 需双因子；request_refund 需幂等键 client_request_id 防重放。
ORDER_TOOLS = [
    {
        "name": "query_order",
        "description": "查询订单详情和状态，需要提供订单编号 + 绑定手机号（双因子校验）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "订单编号，格式 ORD-XXXXXXXX"},
                "phone": {"type": "string", "description": "终端用户手机号（校验后4位匹配订单绑定手机号）"},
                "tenant_id": {"type": "integer", "description": "租户ID（服务端注入）"},
            },
            "required": ["order_no", "phone"],
        },
    },
    {
        "name": "request_refund",
        "description": "申请退款（需审批），退款金额不能超过订单实付金额",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_no": {"type": "string"},
                "phone": {"type": "string", "description": "终端用户手机号（双因子校验，后4位匹配订单绑定手机号）"},
                "amount": {"type": "number"},
                "reason": {"type": "string"},
                "client_request_id": {"type": "string", "description": "幂等键，用于防重"},
                "tenant_id": {"type": "integer"},
            },
            "required": ["order_no", "phone", "amount", "client_request_id"],  # T29: tenant_id 服务端注入
        },
    },
]


def handle_order_tool(tool_name: str, params: dict, *, db: Session | None = None) -> dict:
    """MCP tools/call 分发器：根据 tool_name 调用对应的订单业务逻辑。

    流程:
      1. 决定 Session（外部注入复用 / 内部新建 own_session 负责关闭）
      2. 取 tenant_id
      3. query_order -> 双因子校验查询；request_refund -> order_service 退款（含金额校验+幂等）
      4. 写操作 commit
      5. finally 关闭 own_session

    设计决策:
      与 ticket_server 完全同构的 db 注入与分发模式，保持两个 server 实现一致便于维护。
      退款走 order_service 而非 server 层直写，是因为退款涉及状态机跃迁（订单状态+退款审批流）
      和金额校验，逻辑复杂，集中在 service 层便于复用与测试。

    Returns: 业务结果 dict（由 mcp_service 封装为 JSON-RPC 成功信封）
    Raises: IdentityVerificationError / 业务异常（由 mcp_service 映射错误码）
    """
    own_session = db is None
    if own_session:
        from app.core.database import SessionLocal
        db = SessionLocal()
    try:
        # fail-closed（T11）: tenant_id 由 mcp_service.call_tool 注入，
        # 缺失说明绕过 call_tool 直调 handler，拒绝（正常链路不可能触发）
        tenant_id = params.get("tenant_id")
        if tenant_id is None:
            raise ValueError("missing tenant_id")

        if tool_name == "query_order":
            return _query_order(db, tenant_id, params)

        elif tool_name == "request_refund":
            from app.services.order_service import request_refund
            result = request_refund(
                db, tenant_id=tenant_id, order_no=params["order_no"],
                amount=params["amount"], reason=params.get("reason"),
                client_request_id=params["client_request_id"],
                phone=params.get("phone"),
            )
            db.commit()
            return result

        else:
            raise ValueError(f"未知订单工具: {tool_name}")
    finally:
        if own_session:
            db.close()


def _query_order(db: Session, tenant_id: int, params: dict) -> dict:
    """查询订单详情（含双因子身份校验）。

    流程:
      1. 按 (order_no, tenant_id) 查订单
      2. 不存在 -> IdentityVerificationError
      3. phone 后 4 位匹配校验，不匹配/未绑定 -> 同上统一异常
      4. 返回订单详情（不含 phone）

    设计决策:
      与 _get_ticket_status 完全一致的防枚举策略：不存在与不匹配不可区分，返回值不含 phone。
      双因子校验逻辑集中在 base_server.verify_phone_suffix，两个 server 复用，避免重复实现。

    不存在 / 手机号不匹配 / 未绑定 -> 统一 IdentityVerificationError（防枚举）。
    返回结果不含 phone（避免泄露绑定号）。
    """
    from app.crud.order import get_by_order_no

    order_no = params["order_no"]
    phone = params.get("phone")

    # 按 (order_no, tenant_id) 查询，tenant_id 过滤保证租户隔离
    order = get_by_order_no(db, tenant_id, order_no)
    if order is None:
        # 订单不存在：统一异常防枚举
        raise IdentityVerificationError()
    # 双因子：手机号后 4 位匹配校验
    if not verify_phone_suffix(phone, order.phone):
        raise IdentityVerificationError()

    # 返回详情，不含 phone 字段
    return {
        "order_id": order.id, "order_no": order.order_no,
        "amount": order.amount, "paid_amount": order.paid_amount,
        "status": order.status,
    }
