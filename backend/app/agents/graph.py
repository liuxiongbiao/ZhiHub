"""
backend/app/agents/graph.py

职责: 【多智能体编排核心】LangGraph 状态图定义--State 类型定义、节点注册、条件边（路由分支）、
      图的编译与导出。

核心设计:
  定义 AgentState（TypedDict）作为各节点共享的状态，router 为入口节点，条件边 _route_intent
  按优先级路由（愤怒情绪 > 敏感操作 > 低置信度 > 正常意图）到对应 Agent 节点。
  各 Agent 节点执行完直连 END。009 加了 AGENT_TOOL_MODE 切换：fc 模式注册 ticket_agent_fc_node，
  默认 mcp 模式注册 ticket_agent_node，对比两种工具调用范式。转人工用同步标记 should_interrupt
  而非 true interrupt（同步 invoke 架构不支持 pause/resume）。

关键技术点:
  - AgentState TypedDict: 各节点共享状态，total=False 允许部分字段缺失
  - 条件边优先级: 愤怒 > 敏感 > 低置信度 > 正常，高优先级先返回
  - AGENT_TOOL_MODE 切换: fc/mcp 两种工具调用范式，运行时按配置注册不同节点
  - 同步 invoke 架构: graph.invoke 同步驱动，简单可调试；true interrupt 需 checkpoint+resume
  - 模块级单例: get_graph 惰性编译，避免重复构建

依赖关系:
  - 上游: app/services/agent_service.py（graph.invoke 驱动）
  - 下游: app/agents/nodes/*.py（各节点）、app/config.py（AGENT_TOOL_MODE）

State字段: session_id, tenant_id, messages, intent, confidence, sentiment,
           slots, retrieval_result, current_agent, should_interrupt
节点: router -> {knowledge_agent | ticket_agent | order_agent | human_handoff}
条件边: sentiment=angry 或 命中敏感操作 或 confidence<阈值 -> human_handoff (interrupt)
"""

import logging
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.agents.nodes.human_handoff import human_handoff_node
from app.agents.nodes.knowledge_agent import knowledge_agent_node
from app.agents.nodes.order_agent import order_agent_node
from app.agents.nodes.router import router_node
from app.agents.nodes.ticket_agent import ticket_agent_node

logger = logging.getLogger(__name__)


# ─── State 定义 ───
class AgentState(TypedDict, total=False):
    """LangGraph 智能体状态。

    total=False 表示所有字段可选（节点只写自己负责的字段，初始 state 不必含全字段）。
    各节点只能写入自己负责的字段，条件边只读取不写入。

    字段分组:
      - 会话标识: session_id, tenant_id
      - 对话历史: messages（逐步追加）
      - 路由输出: intent, confidence, sentiment, slots
      - 知识输出: retrieval_result
      - 编排控制: current_agent, should_interrupt
      - 最终输出: final_response
    """
    # 会话标识
    session_id: int
    tenant_id: int
    # 对话历史（逐步追加）
    messages: list[dict[str, str]]
    # 路由节点输出
    intent: str           # knowledge_consult | ticket_query | order_query | sensitive_action
    confidence: float     # 0.0 ~ 1.0
    sentiment: str        # normal | unhappy | angry
    slots: dict[str, str]  # 槽位键值对，如 {"ticket_id": "TK-001"}
    # 知识节点输出
    retrieval_result: list[dict]  # 检索到的文档片段
    # 编排控制
    current_agent: str     # 当前正在执行的节点名
    should_interrupt: bool  # 是否触发中断转人工
    # 最终输出
    final_response: str    # 给用户的回复文本


# ─── 图构建 ───
def build_graph() -> CompiledStateGraph:
    """构建并编译 LangGraph 状态图。

    流程:
      1. StateGraph(AgentState) 建图
      2. 注册节点：router + 4 个 Agent 节点（ticket 按 AGENT_TOOL_MODE 切换 fc/mcp）
      3. set_entry_point("router") 设入口
      4. add_conditional_edges 路由分发（_route_intent 决定下一节点）
      5. 各 Agent 节点 -> END
      6. compile 编译

    设计决策:
      或 FC 版，运行时按配置切换，对比两种工具调用范式。条件边 _route_intent 集中路由逻辑，
      节点本身不决定下一跳（单一职责）。

      START -> router -> route_intent (条件边)
        ├── knowledge_consult -> knowledge_agent -> END
        ├── ticket_query -> ticket_agent -> END
        ├── order_query -> order_agent -> END
        └── angry/sensitive -> human_handoff -> END

                    human_handoff 替换为 interrupt 中断点。
    """
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("router", router_node)
    builder.add_node("knowledge_agent", knowledge_agent_node)
    # 009: 按 AGENT_TOOL_MODE 切换 MCP / Function Calling（默认 mcp）
    from app.config import settings
    if settings.agent_tool_mode == "fc":
        # FC 模式：注册 Function Calling 版工单 Agent，对比 MCP 范式
        from app.agents.nodes.ticket_agent_fc import ticket_agent_fc_node
        builder.add_node("ticket_agent", ticket_agent_fc_node)
    else:
        # MCP 模式（默认）：注册 MCP 协议化工单 Agent
        builder.add_node("ticket_agent", ticket_agent_node)
    builder.add_node("order_agent", order_agent_node)
    builder.add_node("human_handoff", human_handoff_node)

    # 入口: 路由节点
    builder.set_entry_point("router")

    # 条件边: 路由分发（_route_intent 返回值映射到目标节点）
    builder.add_conditional_edges(
        "router",
        _route_intent,
        {
            "knowledge_consult": "knowledge_agent",
            "ticket_query": "ticket_agent",
            "order_query": "order_agent",
            "human_handoff": "human_handoff",
        },
    )

    # 所有 agent 节点执行完后结束（直连 END，无循环）
    builder.add_edge("knowledge_agent", END)
    builder.add_edge("ticket_agent", END)
    builder.add_edge("order_agent", END)
    builder.add_edge("human_handoff", END)

    # demo: 转人工通过 human_handoff_node 设置 should_interrupt 标记 + agent_service
    # 置会话 waiting_human 实现（同步 invoke 架构不支持 true interrupt resume）；
    # 生产可用 interrupt_before=["human_handoff"] + checkpoint 实现 true pause/resume
    return builder.compile()


# ─── 条件边逻辑 ───
def _route_intent(state: AgentState) -> Literal[
    "knowledge_consult", "ticket_query", "order_query", "human_handoff"
]:
    """根据意图和情绪决定下一个节点（条件边路由）。

    决策优先级（高到低，first-match 即返回）:
      1. sentiment=angry -> 强制转人工（不管意图是什么，愤怒用户必须人工安抚）
      2. intent=sensitive_action -> 转人工（敏感操作如退款强制 interrupt）
      3. confidence < 0.4 -> 转人工（低置信度不做自动决策，避免误路由）
      4. 其余按 intent 路由到对应 agent

    设计决策:
      优先级顺序是业务逻辑决定的：愤怒情绪最高（用户体验优先，愤怒用户让 AI 处理可能激化矛盾），
      敏感操作次之（资金安全），低置信度再次（避免误判），最后才是正常意图路由。
      first-match 即返回确保高优先级先判，不会因为 intent 命中而跳过情绪检查。
      confidence 阈值 0.4 与 router 的 FALLBACK_CONFIDENCE(0.3) 配合--fallback 必然转人工。
    """
    intent = state.get("intent", "knowledge_consult")
    sentiment = state.get("sentiment", "normal")
    confidence = state.get("confidence", 0.5)

    # 1. 情绪愤怒 -> 必须转人工（最高优先级，压过一切意图）
    if sentiment == "angry":
        logger.info(f"Route: sentiment=angry -> human_handoff")
        return "human_handoff"

    if intent == "sensitive_action":
        logger.info(f"Route: intent=sensitive_action -> human_handoff")
        return "human_handoff"

    # 3. 低置信度 -> 转人工（不让不确定的意图进入业务节点，避免误调工具）
    if confidence < 0.4:
        logger.info(f"Route: low confidence ({confidence:.2f}) -> human_handoff")
        return "human_handoff"

    # 4. 正常路由：按意图分发到对应 Agent
    # T23: 白名单归一化——LLM 可能输出映射表外的意图（如 "other"），原样返回会让
    # LangGraph 条件边查表 KeyError，被 agent_service 裸兜底吞成泛化报错
    allowed = {"knowledge_consult", "ticket_query", "order_query"}
    if intent not in allowed:
        logger.warning(f"Route: unknown intent {intent!r} -> human_handoff")
        return "human_handoff"
    logger.info(f"Route: intent={intent}, confidence={confidence:.2f} -> {intent}")
    return intent  # type: ignore[return-value]


# ─── 编译好的图实例（模块级单例）───
_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    """获取编译好的 LangGraph 图实例（惰性初始化单例）。

    设计决策:
      惰性单例而非模块加载时编译，是因为编译依赖 settings.agent_tool_mode（运行时配置），
      且首次调用时才编译可推迟可能的导入错误到真正使用时。单例避免每次对话重新编译图的开销。
    """
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
