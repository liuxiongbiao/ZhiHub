"""
backend/app/agents/nodes/knowledge_agent.py

职责: 知识问答 Agent--调 retrieval_service 混合检索 -> LLM 生成带引用回答。

核心设计:
  知识咨询意图的执行节点。取用户消息调 hybrid_search 检索知识库，把检索片段格式化为来源文本，
  拼 prompt 让 LLM 基于来源生成带引用的回答。温度 0.3 比路由高，保留生成灵活性但控制幻觉。
  检索失败或无结果时走兜底回复（建议转人工），不让检索基础设施故障导致空回答。

关键技术点:
  - 混合检索集成: 调 retrieval_service.hybrid_search 取 hybrid_results
  - 带引用回答: _build_sources_text 把检索片段编号格式化，LLM 据此引用 [片段N]
  - 温度策略: 0.3 比路由（0.0）高，生成更自然但不过分发散
  - 历史上下文: 取最近 3 轮（6 条消息）注入 LLM，保持多轮连贯
  - 降级兜底: 检索失败/无结果 -> 友好提示转人工

依赖关系:
  - 上游: app/agents/graph.py（knowledge_agent 节点注册）
  - 下游: app/services/retrieval_service.py（混合检索）、litellm、app/agents/prompts/knowledge.txt
"""

import logging
import os
import time
from typing import Any

from app.core.langfuse_client import observe

logger = logging.getLogger(__name__)

# 知识 Agent 的 LLM 温度（比 router 的 0.0 高，保留生成灵活性，但低于自由对话防幻觉）
KNOWLEDGE_TEMPERATURE = 0.3


def _load_prompt() -> str:
    """加载知识问答 prompt 模板（含 {query}/{sources} 占位符）。"""
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "knowledge.txt")
    with open(prompt_path, encoding="utf-8") as f:
        return f.read()


def _build_sources_text(retrieval_result: list[dict]) -> str:
    """将检索结果格式化为 prompt 中的来源文本。

    流程:
      1. 无结果 -> 返回"（无检索结果）"
      2. 每个片段编号 [片段N]，标注文档名 + 切片正文

    设计决策:
      编号格式 [片段N] 是为了让 LLM 在回答里能引用来源（如"根据[片段1]..."），
      增强回答可追溯性，这是 RAG 带引用回答的标准做法。

    Args:
        retrieval_result: [{"doc_filename": "x.pdf", "chunk_text": "...", "chunk_index": 0}, ...]
    Returns:
        格式化的来源文本，每个片段标注文档名和序号
    """
    if not retrieval_result:
        return "（无检索结果）"

    parts = []
    for i, item in enumerate(retrieval_result, 1):
        doc_name = item.get("doc_filename", "未知文档")
        chunk_text = item.get("chunk_text", "")
        parts.append(f"[片段{i}] 来源: {doc_name}\n{chunk_text}")

    return "\n\n".join(parts)


def _generate_answer(query: str, sources_text: str, history: list[dict]) -> tuple[str, bool]:
    """调用 LLM 生成带引用的回答。

    流程:
      1. 加载 prompt 模板，填入 query + sources
      2. 构建 messages：system + 最近 3 轮历史 + 当前 prompt
      3. litellm completion（temperature=0.3，max_tokens=800）
      4. 异常 -> 兜底回复

    设计决策:
      历史取最近 6 条（3 轮 user+assistant），兼顾上下文连贯与 token 预算。
      max_tokens=800 限制回答长度，避免 LLM 啰嗦。注意 query 已由 retrieval_service 内部
      做过指代改写，这里直接用原 query 拼 prompt（改写后的用于检索）。

    Args:
        query: 用户查询
        sources_text: 格式化的来源文本
        history: 对话历史（最近几轮）
    Returns:
        (回答文本, 是否生成成功)。生成失败时返回兜底文案 + False，
        供上层标记"非有效回复"（：失败兜底不计入配额）。
    """
    prompt_template = _load_prompt()
    full_prompt = prompt_template.format(query=query, sources=sources_text)

    try:
        from app.core.llm_client import llm_complete

        # 构建 messages（包含历史上下文）
        messages = [{"role": "system", "content": "你是一个专业的客服知识助手。"}]
        # 加入最近 3 轮历史（3 轮 = 6 条消息 user + assistant）
        for m in history[-6:]:
            messages.append(m)

        # 当前完整 prompt
        messages.append({"role": "user", "content": full_prompt})

        resp = llm_complete(
            messages=messages,
            max_tokens=800,
            temperature=KNOWLEDGE_TEMPERATURE,
        )
        answer = resp.choices[0].message.content.strip()
        logger.info(f"Knowledge Agent generated answer ({len(answer)} chars)")
        return answer, True
    except Exception as e:
        # LLM 失败兜底，不让生成故障导致空回复
        logger.error(f"Knowledge Agent LLM call failed: {e}")
        return "抱歉，系统处理您的请求时遇到了问题，请稍后重试。", False


@observe(name="knowledge_agent")
def knowledge_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点：知识问答处理。

    流程:
      1. 从 state 取最新用户消息 + tenant_id
      2. 调 retrieval_service.hybrid_search 混合检索
      3. 有结果 -> _generate_answer 生成带引用回答
      4. 无结果 -> 兜底回复（建议转人工）

    设计决策:
      hybrid_search 异常时 retrieval_result 置空走兜底，不让检索基础设施故障（Milvus/ES 没起）
      导致节点抛异常。返回 current_agent="knowledge_agent" 供 trace 记录。

    Args:
        state: AgentState dict
    Returns:
        {"final_response": str, "retrieval_result": list[dict], "current_agent": str}
    """
    start = time.perf_counter()
    messages: list[dict] = state.get("messages", [])
    tenant_id: int | None = state.get("tenant_id")  # : 不默认租户 1，缺失则 None

    # tenant_id 缺失（state 异常）→ 不调检索（防 None 拼进 Milvus expr / 跨租户），
    # 返回降级提示。正常流程 handle_message 恒注入 tenant_id，不会走到这里。
    if tenant_id is None:
        return {"final_response": "系统暂时无法处理您的请求，请稍后重试。", "retrieval_result": []}

    # 倒序取最后一条用户消息
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m["content"]
            break

    if not last_user:
        return {"final_response": "请输入您的问题。"}

    retrieval_result = []

    try:
        from app.services.retrieval_service import hybrid_search

        result = hybrid_search(
            query=last_user,
            tenant_id=tenant_id,
            history=messages,
        )

        # PRD §3.3：HyDE 兜底后仍低置信度 → 强制转人工（防止硬编答案）
        if result.low_confidence:
            return {
                "final_response": "当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。",
                "retrieval_result": [],
                "current_agent": "knowledge_agent",
                "should_interrupt": True,
            }

        # 把 ChunkResult 转为 dict 列表供 trace 和 sources 展示
        retrieval_result = [
            {
                "doc_filename": r.doc_filename or "未知文档",
                "chunk_text": r.chunk_text,
                "chunk_index": r.chunk_index,
                "score": r.score,
            }
            for r in result.hybrid_results
        ]
    except Exception as e:
        # 检索失败（可能 Milvus/ES 未启动或空库）：置空走兜底，不抛异常
        logger.warning(f"Retrieval failed (may be empty KB or missing infra): {e}")
        retrieval_result = []

    # 2. 生成回答
    if state.get("skip_generation"):
        # 流式路径跳过生成（由流式端点统一生成），只返回检索结果
        answer = "" if retrieval_result else "暂未找到相关信息，建议联系人工客服获取帮助。"
        gen_ok = True
    elif retrieval_result:
        sources_text = _build_sources_text(retrieval_result)
        # history 传入时去掉最后一条（当前 user 消息），避免重复
        answer, gen_ok = _generate_answer(last_user, sources_text, messages[:-1])
    else:
        # 无检索结果 → 兜底，建议转人工
        answer = "暂未找到相关信息，建议联系人工客服获取帮助。"
        gen_ok = True

    elapsed = int((time.perf_counter() - start) * 1000)
    logger.info(f"Knowledge Agent finished in {elapsed}ms")

    # PRD §3.4：LLM 生成失败 → 转人工（不计费由 generation_failed 标记保证）
    interrupted = not gen_ok
    if interrupted:
        answer = "当前系统繁忙，已为您转接人工客服，请稍候。"

    return {
        "final_response": answer,
        "retrieval_result": retrieval_result,
        "current_agent": "knowledge_agent",
        "generation_failed": interrupted,
        "should_interrupt": interrupted,
    }
