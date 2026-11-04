"""工单/订单节点共用的槽位反问和 MCP 错误回复。"""

from typing import Any

from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN


def last_user_content(messages: list[dict]) -> str:
    """取最近一条用户消息。没有用户消息时返回空字符串。"""
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content") or ""
    return ""


def missing_slot_reply(agent_name: str, slots: dict, prompt: str) -> dict[str, Any]:
    """缺槽位时反问，不调用工具。"""
    return {
        "final_response": prompt,
        "slots": slots,
        "current_agent": agent_name,
    }


def mcp_failure_reply(
    response: dict,
    *,
    agent_name: str,
    circuit_default: str,
    mismatch: str,
) -> dict[str, Any] | None:
    """错误信封转回复。成功信封返回 None，由调用方继续生成业务文案。"""
    if "error" not in response:
        return None
    error = response["error"]
    if error.get("code") == ERR_CIRCUIT_BROKEN:
        return {
            "final_response": error.get("message", circuit_default),
            "current_agent": agent_name,
            "should_interrupt": True,
        }
    return {
        "final_response": mismatch,
        "current_agent": agent_name,
    }


def tool_exception_reply(agent_name: str, prompt: str) -> dict[str, Any]:
    """工具调用抛错时的兜底回复。"""
    return {
        "final_response": prompt,
        "current_agent": agent_name,
        "generation_failed": True,
    }
