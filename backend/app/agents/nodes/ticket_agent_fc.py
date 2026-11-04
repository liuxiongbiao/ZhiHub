"""
backend/app/agents/nodes/ticket_agent_fc.py

职责: Function Calling 版工单 Agent（规格 009 US1）-- 与 MCP ticket_agent 等价，
      但工具分发走 LLM 原生 tool_calls 而非 MCP 协议信封。复用 规格 006 双因子/权限/熔断（工具执行仍调
      mcp_service.call_tool 信封入口）。

核心设计:
  这是 MCP vs FC 对比的 FC 侧实现。两者槽位收集、双因子校验、工具执行完全一致（复用 ticket_agent
  的 _extract_phone/_is_valid_phone，工具执行都走 call_tool 信封入口），唯一差异是"谁决定调工具"：
  MCP 版是 Agent 代码直接调 call_tool（Agent 决定），FC 版是 LLM 返回 tool_calls 再由 Agent 执行
  （LLM 决定）。这样对比公平，差异纯粹在分发方式，凸显 MCP 的协议化/可审计/与 LLM 解耦优势。

关键技术点:
  - litellm tools 参数: _build_tools 返回 OpenAI tools 格式 schema，DeepSeek 兼容
  - tool_calls 流程: LLM 返回 tool_calls -> Agent 执行（FC 核心：LLM 决定调工具）
  - 复用 规格 006 安全加固: 工具执行仍走 call_tool，双因子/权限/熔断完全一致
  - tool_choice="auto": 让 LLM 自主决定是否调工具

  MCP:  Agent 直接调 call_tool（信封标准化、与 LLM 解耦、可审计、跨 LLM 通用）
  FC:   LLM 返回 tool_calls -> Agent 执行（LLM 原生、简单但紧耦合、无标准信封、依赖特定 LLM 能力）
  两者工具执行层相同（复用 规格 006 安全加固），仅分发方式不同。

依赖关系:
  - 上游: app/agents/graph.py（AGENT_TOOL_MODE=fc 时注册此节点）
  - 下游: app/agents/nodes/ticket_agent.py（复用 phone helper）、litellm、mcp_service
"""

import logging
from typing import Any

from app.core.langfuse_client import observe

# 复用 MCP 版的 phone 提取逻辑（DRY，双因子校验一致）
from app.agents.nodes.slot_dialog import mcp_failure_reply
from app.agents.nodes.ticket_agent import format_ticket_reply, resolve_ticket_query

logger = logging.getLogger(__name__)


def _build_tools() -> list[dict]:
    """定义 Function Calling tools schema（OpenAI tools 格式，DeepSeek 兼容）。

    设计决策:
      schema 与 MCP 版 TICKET_TOOLS 的 inputSchema 等价，只是外层包了 {"type":"function","function":{...}}
      的 OpenAI tools 格式。这样 FC 和 MCP 两种模式对 LLM 暴露的工具能力完全一致，对比才公平。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "get_ticket_status",
                "description": "查询工单状态和详情，需要提供工单编号 + 绑定手机号（双因子校验）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticket_no": {
                            "type": "string",
                            "description": "工单编号，格式 TK-XXXXXXXX",
                        },
                        "phone": {
                            "type": "string",
                            "description": "终端用户手机号（校验后4位匹配工单绑定手机号）",
                        },
                    },
                    "required": ["ticket_no", "phone"],
                },
            },
        }
    ]


def _ticket_tool_reply(ticket_no: str, phone: str, tenant_id: int | None) -> dict[str, Any]:
    """执行模型选出的工单工具。协议、权限和熔断仍走 MCP 信封。"""
    from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN, build_request
    from app.services.mcp_service import call_tool

    env = build_request(
        "get_ticket_status", {"ticket_no": ticket_no, "phone": phone}
    )
    result = call_tool("agent", env, tenant_id=tenant_id)

    if "error" in result:
        failure = mcp_failure_reply(
            result,
            agent_name="ticket_agent_fc",
            circuit_default="工单查询服务暂时不可用，已转接人工客服。",
            mismatch="未查询到匹配信息，请确认工单号和手机号是否正确。",
        )
        if failure is not None:
            return failure

    return {
        "final_response": format_ticket_reply(result["result"]),
        "current_agent": "ticket_agent_fc",
    }


def _query_ticket_by_function_call(ticket_no: str, phone: str, tenant_id: int | None) -> dict[str, Any]:
    """让模型决定是否调用工单工具，再按原有文案返回结果或兜底。"""
    try:
        import litellm

        resp = litellm.completion(
            model="deepseek/deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"你是工单查询助手。用户要查工单 {ticket_no}，手机号 {phone}。"
                        "请调用 get_ticket_status 工具查询工单状态。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"查工单 {ticket_no}，手机号 {phone}",
                },
            ],
            tools=_build_tools(),
            tool_choice="auto",  # 让 LLM 自主决定是否调工具
            max_tokens=200,
            temperature=0.0,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return _ticket_tool_reply(ticket_no, phone, tenant_id)
        return {
            "final_response": "查询工单时遇到问题，请稍后重试。",
            "current_agent": "ticket_agent_fc",
        }
    except Exception as e:
        logger.error(f"FC agent failed: {e}")
        return {
            "final_response": "查询工单时遇到问题，请稍后重试。",
            "current_agent": "ticket_agent_fc",
        }


@observe(name="ticket_agent_fc")
def ticket_agent_fc_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点: Function Calling 版工单查询。

    流程:
      1. 提取 ticket_no + phone（与 MCP 版一致，缺 phone 反问）
      2. LLM Function Calling（传 tools 参数，LLM 决定调工具）
      3. 解析 tool_calls -> 调 mcp_service.call_tool（**复用 规格 006 双因子/权限/熔断**）
      4. 结果格式化（与 MCP 版一致）

    设计决策:
      槽位收集与 MCP 版完全一致（复用 helper），保证双因子校验不因模式切换而绕过。
      FC 的核心在第 2 步：把 ticket_no/phone 喂给 LLM，让 LLM 决定调 get_ticket_status，
      而非 Agent 代码直接调。这模拟了"LLM 自主工具选择"的 FC 范式。工具执行仍走 call_tool，
      所以 规格 006 的安全加固（权限/熔断/双因子）完全复用，两种模式安全等价。
      注意：此处槽位已由 Agent 预先收集齐全才喂 LLM，LLM 实际几乎必然调工具，
      FC 的"自主性"在此场景被弱化--这是为了与 MCP 版公平对比的简化。

    Args:
        state: AgentState dict
    Returns:
        {final_response, current_agent, [slots], [should_interrupt]}
    """
    messages: list[dict] = state.get("messages", [])
    slots: dict = state.get("slots", {})
    tenant_id: int | None = state.get("tenant_id")  # : 不默认租户 1，缺失则 None 让 call_tool fail-closed

    reply, ticket_no, phone = resolve_ticket_query(messages, slots, "ticket_agent_fc")
    if reply is not None:
        return reply
    return _query_ticket_by_function_call(ticket_no, phone, tenant_id)
