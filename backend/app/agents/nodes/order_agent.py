"""
backend/app/agents/nodes/order_agent.py

职责: 订单 Agent--槽位检查（order_no + phone 双因子）-> MCP 调用订单工具 -> 生成回复。

核心设计:
  与 ticket_agent 同构：槽位收集循环（order_no + phone 双因子），缺槽位反问不调工具（FR-004），
  双齐后走 MCP 信封调用 query_order。phone 是 006 加的双因子槽位。订单状态机比工单复杂
  （7 态：created/paid/shipped/completed/cancelled/refunding/refunded），状态映射更全。

关键技术点:
  - 槽位收集循环: 同 ticket_agent，缺槽位反问不调工具
  - 双因子槽位: order_no + phone
  - MCP 信封调用: build_request + call_tool，复用 006 安全加固
  - 7 态状态映射: 订单状态机完整中文映射
  - 错误处理: 熔断转人工，身份校验失败防枚举统一提示

依赖关系:
  - 上游: app/agents/graph.py（order_agent 节点注册）
  - 下游: app/services/mcp_service.py（call_tool）、app/mcp_servers/base_server.py
"""

import logging
import re
from typing import Any

from app.core.langfuse_client import observe
from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN, build_request

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"1\d{10}")


def _extract_phone(messages: list[dict], slots: dict) -> str:
    """从 slots 或最近用户消息提取手机号（与 ticket_agent 同逻辑）。"""
    phone = slots.get("phone", "").strip()
    if phone:
        return phone
    for m in reversed(messages):
        if m.get("role") == "user":
            match = _PHONE_RE.search(m["content"])
            if match:
                return match.group(0)
            break
    return ""


def _is_valid_phone(phone: str) -> bool:
    """校验手机号格式：非空 + 纯数字 + 11 位。"""
    return bool(phone) and phone.isdigit() and len(phone) == 11


@observe(name="order_agent")
def order_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点: 订单查询。

    流程:
      1. 提取 order_no（slots 或消息正则 ORD-XXXXXXXX）
      2. 提取 phone（slots 或消息正则，双因子）
      3. 缺 order_no -> 反问订单号
      4. 缺/格式错 phone -> 反问手机号（FR-004：不调工具）
      5. 双齐 -> build_request 构造信封 -> call_tool 调 query_order
      6. 解析响应信封 -> 自然语言回复

    设计决策:
      与 ticket_agent 完全同构的槽位收集 + MCP 调用模式，差异仅在工具名和状态映射。
      订单状态机 7 态（含退款相关 refunding/refunded），映射比工单 4 态更全。
      退款等敏感操作本应转人工（sensitive_action 意图在路由层已拦截转人工），
      此节点只处理查询，不直接发起退款。

    Args:
        state: AgentState dict
    Returns:
        {final_response, current_agent, [slots], [should_interrupt]}
    """
    messages: list[dict] = state.get("messages", [])
    slots: dict = state.get("slots", {})
    tenant_id: int | None = state.get("tenant_id")  # M9: 不默认租户 1，缺失则 None 让 call_tool fail-closed

    # 1. 提取订单号（slots 优先，否则扫消息正则）
    order_no = slots.get("order_no", "").strip()
    if not order_no:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m["content"]
                break
        match = re.search(r"ORD-[A-Z0-9]+", last_user, re.IGNORECASE)
        if match:
            order_no = match.group(0)
            slots["order_no"] = order_no

    # 缺订单号 -> 反问
    if not order_no:
        return {
            "final_response": "请提供您的订单编号（格式：ORD-XXXXXXXX），以便我为您查询订单信息。",
            "slots": slots,
            "current_agent": "order_agent",
        }

    # 2. 提取手机号（双因子）
    phone = _extract_phone(messages, slots)
    if phone:
        slots["phone"] = phone

    # 3. 缺/格式错手机号 -> 反问（FR-004：不调工具）
    if not _is_valid_phone(phone):
        return {
            "final_response": "为保护您的账户安全，查询订单需验证身份。请提供您在订单中绑定的手机号（11位）。",
            "slots": slots,
            "current_agent": "order_agent",
        }

    # 4. 双槽齐全 -> 调用 MCP（JSON-RPC 信封）
    try:
        from app.services.mcp_service import call_tool

        env = build_request(
            "query_order",
            {"order_no": order_no, "phone": phone},
        )
        resp = call_tool("agent", env, tenant_id=tenant_id)

        if "error" in resp:
            err = resp["error"]
            # 熔断兜底（US4）：工具熔断中 -> 兜底话术 + 转人工
            if err.get("code") == ERR_CIRCUIT_BROKEN:
                return {
                    "final_response": err.get("message", "订单查询服务暂时不可用，已转接人工客服。"),
                    "current_agent": "order_agent",
                    "should_interrupt": True,
                }
            # 身份校验失败统一提示，防枚举
            return {
                "final_response": "未查询到匹配信息，请确认订单号和手机号是否正确。",
                "current_agent": "order_agent",
            }

        # success 信封：7 态状态映射 + 金额信息
        data = resp["result"]
        status_map = {
            "created": "待支付", "paid": "已支付", "shipped": "已发货",
            "completed": "已完成", "cancelled": "已取消",
            "refunding": "退款中", "refunded": "已退款",
        }
        status_cn = status_map.get(data.get("status", ""), data.get("status", ""))

        response = (
            f"订单 {data['order_no']} 详情：\n"
            f"状态：{status_cn}\n"
            f"金额：{data.get('amount', 0):.2f} 元\n"
            f"实付：{data.get('paid_amount', 0):.2f} 元"
        )
        return {"final_response": response, "current_agent": "order_agent"}

    except Exception as e:
        logger.error(f"Order agent MCP call failed: {e}")
        return {
            "final_response": "查询订单时遇到问题，请稍后重试。",
            "current_agent": "order_agent",
        }
