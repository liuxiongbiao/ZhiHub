"""
backend/app/agents/nodes/human_handoff.py

职责: 转人工节点--汇总所有转人工触发条件 -> 置 should_interrupt 标记 -> 生成用户提示。

核心设计:
  Human-in-the-loop 的实现节点。汇总转人工触发原因（愤怒情绪/敏感操作/低置信度），
  生成带原因说明的用户提示，置 should_interrupt=True。用同步标记而非 LangGraph 的 true
  interrupt/resume，原因是项目用同步 invoke 架构（graph.invoke 同步），true interrupt 需要
  checkpoint 持久化 + resume 机制，复杂度高；同步标记 + agent_service 置会话 waiting_human
  即可实现"暂停 AI、等坐席认领"的效果，demo 规模够用且可讲清原理。

关键技术点:
  - should_interrupt 同步标记: 置 True 后 agent_service 据此置会话 waiting_human，等坐席认领
  - 触发条件汇总: 愤怒情绪/敏感操作/低置信度，对齐 graph.py _route_intent 的路由逻辑
  - 为什么不用 true interrupt: 同步 invoke 架构不支持，需 checkpoint+resume，复杂度高

依赖关系:
  - 上游: app/agents/graph.py（human_handoff 节点注册，条件边路由进入）
  - 下游: app/services/agent_service.py（读 should_interrupt 置会话 waiting_human）

触发条件（来自 router + graph.py 条件边）:
  1. sentiment=angry -> 情绪愤怒
  2. intent=sensitive_action -> 敏感操作(如退款)强制转人工
  3. confidence<0.4 -> 路由不确定
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def human_handoff_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点: 转人工处理。

    流程:
      1. 从 state 读 sentiment/intent/confidence
      2. 汇总触发原因（愤怒/敏感/低置信度）
      3. 生成带原因的用户提示
      4. 置 should_interrupt=True（agent_service 据此置会话 waiting_human）

    设计决策:
      触发原因汇总而非单一原因，是因为可能多个条件同时命中（如既愤怒又低置信度），
      一次性告知用户原因更透明。should_interrupt=True 是同步标记，agent_service 读到后
      置会话 waiting_human 等坐席认领，而非 LangGraph 的 true interrupt（需 checkpoint resume）。
      通知坐席在 demo 简化为日志，生产应发布到坐席工作台（RabbitMQ/WS）。

    将触发原因汇总，更新会话状态为 waiting_human，
    返回用户提示信息并发布通知（demo 简化：仅写日志）。
    """
    sentiment = state.get("sentiment", "normal")
    intent = state.get("intent", "")
    confidence = state.get("confidence", 0.0)
    session_id = state.get("session_id", 0)

    # 确定触发原因（对齐 graph.py _route_intent 的路由条件）
    reasons = []
    if sentiment == "angry":
        reasons.append("用户情绪愤怒")
    if intent == "sensitive_action":
        reasons.append("涉及敏感操作（退款/投诉等）")
    if confidence < 0.4:
        reasons.append(f"AI置信度不足 ({confidence:.0%})")

    reason_str = "、".join(reasons) if reasons else "系统判定需人工介入"
    logger.info(f"Human handoff: session={session_id}, reasons={reason_str}")

    # 生成用户提示（告知原因 + 等待说明 + 兜底建单）
    response = (
        f"您的问题因以下原因已转接人工客服：{reason_str}。\n"
        "请稍候，我们的坐席将尽快为您服务。\n"
        "如长时间无人响应，您的消息将自动创建工单。"
    )

    return {
        "final_response": response,
        "should_interrupt": True,  # 同步标记，agent_service 据此置会话 waiting_human
        "current_agent": "human_handoff",
    }
