"""
backend/app/agents/nodes/ticket_agent_fc.py

职责: Function Calling 版工单 Agent（009-tech-compare-agent-enhance US1）-- 与 MCP ticket_agent 等价，
      但工具分发走 LLM 原生 tool_calls 而非 MCP 协议信封。复用 006 双因子/权限/熔断（工具执行仍调
      mcp_service.call_tool 信封入口）。

核心设计:
  这是 MCP vs FC 对比的 FC 侧实现。两者槽位收集、双因子校验、工具执行完全一致（复用 ticket_agent
  的 _extract_phone/_is_valid_phone，工具执行都走 call_tool 信封入口），唯一差异是"谁决定调工具"：
  MCP 版是 Agent 代码直接调 call_tool（Agent 决定），FC 版是 LLM 返回 tool_calls 再由 Agent 执行
  （LLM 决定）。这样对比公平，差异纯粹在分发方式，凸显 MCP 的协议化/可审计/与 LLM 解耦优势。

关键技术点:
  - litellm tools 参数: _build_tools 返回 OpenAI tools 格式 schema，DeepSeek 兼容
  - tool_calls 流程: LLM 返回 tool_calls -> Agent 执行（FC 核心：LLM 决定调工具）
  - 复用 006 安全加固: 工具执行仍走 call_tool，双因子/权限/熔断完全一致
  - tool_choice="auto": 让 LLM 自主决定是否调工具

  MCP:  Agent 直接调 call_tool（信封标准化、与 LLM 解耦、可审计、跨 LLM 通用）
  FC:   LLM 返回 tool_calls -> Agent 执行（LLM 原生、简单但紧耦合、无标准信封、依赖特定 LLM 能力）
  两者工具执行层相同（复用 006 安全加固），仅分发方式不同。

依赖关系:
  - 上游: app/agents/graph.py（AGENT_TOOL_MODE=fc 时注册此节点）
  - 下游: app/agents/nodes/ticket_agent.py（复用 phone helper）、litellm、mcp_service
"""

import logging
import re
from typing import Any

from app.core.langfuse_client import observe

# 复用 MCP 版的 phone 提取逻辑（DRY，双因子校验一致）
from app.agents.nodes.ticket_agent import _extract_phone, _is_valid_phone

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


@observe(name="ticket_agent_fc")
def ticket_agent_fc_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点: Function Calling 版工单查询。

    流程:
      1. 提取 ticket_no + phone（与 MCP 版一致，缺 phone 反问）
      2. LLM Function Calling（传 tools 参数，LLM 决定调工具）
      3. 解析 tool_calls -> 调 mcp_service.call_tool（**复用 006 双因子/权限/熔断**）
      4. 结果格式化（与 MCP 版一致）

    设计决策:
      槽位收集与 MCP 版完全一致（复用 helper），保证双因子校验不因模式切换而绕过。
      FC 的核心在第 2 步：把 ticket_no/phone 喂给 LLM，让 LLM 决定调 get_ticket_status，
      而非 Agent 代码直接调。这模拟了"LLM 自主工具选择"的 FC 范式。工具执行仍走 call_tool，
      所以 006 的安全加固（权限/熔断/双因子）完全复用，两种模式安全等价。
      注意：此处槽位已由 Agent 预先收集齐全才喂 LLM，LLM 实际几乎必然调工具，
      FC 的"自主性"在此场景被弱化--这是为了与 MCP 版公平对比的简化。

    Args:
        state: AgentState dict
    Returns:
        {final_response, current_agent, [slots], [should_interrupt]}
    """
    messages: list[dict] = state.get("messages", [])
    slots: dict = state.get("slots", {})
    tenant_id: int | None = state.get("tenant_id")  # M9: 不默认租户 1，缺失则 None 让 call_tool fail-closed

    # 1. 提取工单号（与 MCP 版逻辑一致）
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

    if not ticket_no:
        return {
            "final_response": "请提供您的工单编号（格式：TK-XXXXXXXX），以便我为您查询工单进度。",
            "slots": slots,
            "current_agent": "ticket_agent_fc",
        }

    # 2. 提取手机号（复用 MCP 版 helper，双因子校验一致）
    phone = _extract_phone(messages, slots)
    if phone:
        slots["phone"] = phone

    if not _is_valid_phone(phone):
        return {
            "final_response": "为保护您的账户安全，查询工单需验证身份。请提供您在工单中绑定的手机号（11位）。",
            "slots": slots,
            "current_agent": "ticket_agent_fc",
        }

    # 3. LLM Function Calling（FC 核心：LLM 决定调工具，而非 Agent 直接调）
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
            # 4. 执行工具调用（复用 006 信封入口 + 双因子/权限/熔断）
            # 注意：FC 版工具执行仍走 call_tool，与 MCP 版安全等价
            from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN, build_request
            from app.services.mcp_service import call_tool

            env = build_request(
                "get_ticket_status", {"ticket_no": ticket_no, "phone": phone}
            )
            result = call_tool("agent", env, tenant_id=tenant_id)

            if "error" in result:
                err = result["error"]
                if err.get("code") == ERR_CIRCUIT_BROKEN:
                    return {
                        "final_response": err.get(
                            "message", "工单查询服务暂时不可用，已转接人工客服。"
                        ),
                        "current_agent": "ticket_agent_fc",
                        "should_interrupt": True,
                    }
                # 身份校验失败统一提示，防枚举
                return {
                    "final_response": "未查询到匹配信息，请确认工单号和手机号是否正确。",
                    "current_agent": "ticket_agent_fc",
                }

            data = result["result"]
            status_map = {
                "open": "待处理",
                "in_progress": "处理中",
                "resolved": "已解决",
                "closed": "已关闭",
            }
            status_cn = status_map.get(data.get("status", ""), data.get("status", ""))

            response = (
                f"工单 {data['ticket_no']} 的当前状态：{status_cn}\n"
                f"标题：{data.get('title', '无')}\n"
                f"优先级：{data.get('priority', '无')}"
            )
            return {"final_response": response, "current_agent": "ticket_agent_fc"}
        else:
            # LLM 没返回 tool_call -> 兜底（理论上预填了槽位 LLM 应该会调）
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
