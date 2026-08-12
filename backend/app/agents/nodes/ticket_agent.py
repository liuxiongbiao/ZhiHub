"""
backend/app/agents/nodes/ticket_agent.py

职责: 工单 Agent--槽位检查（ticket_no + phone 双因子，缺槽位反问不调工具）-> MCP 调用工单工具
      -> 结构化结果转自然语言回复。

核心设计:
  典型的槽位收集循环：先从 slots 和消息正则提取 ticket_no 和 phone，缺哪个反问哪个，
  双齐后才调 MCP 工具（FR-004：缺槽位不调工具，避免无效调用）。phone 是 006 加的双因子槽位--
  查工单需手机号+工单号双因子，缺 phone 反问，不调工具。工具调用走 JSON-RPC 信封（build_request
  构造，mcp_service.call_tool 执行），复用 006 的权限/熔断/双因子安全加固。

关键技术点:
  - 槽位收集循环: 缺槽位反问，不调工具（FR-004），多轮逐步补全
  - 双因子槽位: ticket_no + phone，缺 phone 反问（006 安全加固）
  - MCP 信封调用: build_request 构造请求，call_tool 执行，解析响应信封
  - 错误处理: 熔断(-32001) -> 兜底+转人工；身份校验失败(-32602) -> 统一友好提示防枚举
  - 结果转自然语言: status_map 把英文状态映射中文，拼友好回复

依赖关系:
  - 上游: app/agents/graph.py（ticket_agent 节点注册，MCP 模式）
  - 下游: app/services/mcp_service.py（call_tool）、app/mcp_servers/base_server.py（信封+错误码）
"""

import logging
import re
from typing import Any

from app.core.langfuse_client import observe
from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN, build_request

logger = logging.getLogger(__name__)

# 手机号格式：11 位数字（终端用户提供，校验后4位匹配工单绑定号）
# 注意这里用 1\d{10} 宽松匹配，更严格的 1[3-9]\d{9} 在 desensitize.py（脱敏用）
_PHONE_RE = re.compile(r"1\d{10}")


def _extract_phone(messages: list[dict], slots: dict) -> str:
    """从 slots 或最近用户消息中提取手机号。

    流程:
      1. slots 已有 phone -> 直接返回
      2. 否则倒序找最近用户消息，正则匹配 11 位手机号

    设计决策:
      优先从 slots 取（router 已提取的槽位），slots 没有才扫消息，避免重复提取。
      只扫最近一条用户消息而非全部，因为手机号通常在当前轮提供。
    """
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


@observe(name="ticket_agent")
def ticket_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点: 工单查询/创建（MCP 模式）。

    流程:
      1. 提取 ticket_no（slots 或消息正则 TK-XXXXXXXX）
      2. 提取 phone（slots 或消息正则，双因子）
      3. 缺 ticket_no -> 反问工单号
      4. 缺/格式错 phone -> 反问手机号（FR-004：不调工具）
      5. 双齐 -> build_request 构造信封 -> call_tool 调 get_ticket_status
      6. 解析响应：error 信封按错误码处理，success 信封转自然语言

    设计决策:
      缺槽位反问而非调用工具，是 FR-004 的要求--缺 phone 调工具会直接 -32602 失败，
      反问让用户补全后一次成功，体验更好且减少无效工具调用。熔断错误码 -32001 触发转人工
      （should_interrupt=True），身份校验失败 -32602 返回统一"未查询到匹配信息"防枚举。

    Args:
        state: AgentState dict
    Returns:
        {final_response, current_agent, [slots], [should_interrupt]}
    """
    messages: list[dict] = state.get("messages", [])
    slots: dict = state.get("slots", {})
    tenant_id: int | None = state.get("tenant_id")  # M9: 不默认租户 1，缺失则 None 让 call_tool fail-closed

    # 1. 提取工单号（slots 优先，否则扫消息正则）
    ticket_no = slots.get("ticket_no", "").strip()
    if not ticket_no:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m["content"]
                break
        match = re.search(r"TK-[A-Z0-9]+", last_user, re.IGNORECASE)
        if match:
            ticket_no = match.group(0)
            slots["ticket_no"] = ticket_no

    # 缺工单号 -> 反问（不调工具）
    if not ticket_no:
        return {
            "final_response": "请提供您的工单编号（格式：TK-XXXXXXXX），以便我为您查询工单进度。",
            "slots": slots,
            "current_agent": "ticket_agent",
        }

    # 2. 提取手机号（双因子）
    phone = _extract_phone(messages, slots)
    if phone:
        slots["phone"] = phone

    # 3. 缺/格式错手机号 -> 反问（FR-004：不调用查询工具）
    if not _is_valid_phone(phone):
        return {
            "final_response": "为保护您的账户安全，查询工单需验证身份。请提供您在工单中绑定的手机号（11位）。",
            "slots": slots,
            "current_agent": "ticket_agent",
        }

    # 4. 双槽齐全 -> 调用 MCP（JSON-RPC 信封）
    try:
        from app.services.mcp_service import call_tool

        # 构造 JSON-RPC 请求信封（tenant_id 由 call_tool 注入，这里不传）
        env = build_request(
            "get_ticket_status",
            {"ticket_no": ticket_no, "phone": phone},
        )
        resp = call_tool("agent", env, tenant_id=tenant_id)

        if "error" in resp:
            err = resp["error"]
            # 熔断兜底（US4）：工具熔断中 -> 兜底话术 + 转人工
            if err.get("code") == ERR_CIRCUIT_BROKEN:
                return {
                    "final_response": err.get("message", "工单查询服务暂时不可用，已转接人工客服。"),
                    "current_agent": "ticket_agent",
                    "should_interrupt": True,
                }
            # -32602 身份校验失败/不存在 -> 统一友好提示（不泄露工单是否存在，防枚举）
            return {
                "final_response": "未查询到匹配信息，请确认工单号和手机号是否正确。",
                "current_agent": "ticket_agent",
            }

        # success 信封：转自然语言回复
        data = resp["result"]
        status_map = {"open": "待处理", "in_progress": "处理中", "resolved": "已解决", "closed": "已关闭"}
        status_cn = status_map.get(data.get("status", ""), data.get("status", ""))

        response = (
            f"工单 {data['ticket_no']} 的当前状态：{status_cn}\n"
            f"标题：{data.get('title', '无')}\n"
            f"优先级：{data.get('priority', '无')}"
        )
        return {"final_response": response, "current_agent": "ticket_agent"}

    except Exception as e:
        # 调用异常兜底
        logger.error(f"Ticket agent MCP call failed: {e}")
        return {
            "final_response": "查询工单时遇到问题，请稍后重试。",
            "current_agent": "ticket_agent",
        }
