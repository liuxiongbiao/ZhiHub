"""
backend/app/agents/deep_agent/planner.py

职责: DeepAgent 自主规划器 -- ReAct 循环（LLM 自主决策工具调用）
      查 MySQL 统计 / 检索知识库 SOP / 综合生成报告 -> 工具调用次数熔断。

核心设计:
  ReAct (Reasoning + Acting) 循环: LLM 自主决定调用哪个工具、何时结束。
  与 LangGraph 预定义状态图形成对比 -- DeepAgent 是"自主规划执行"范式。
  每轮: LLM 生成 Thought + Action + Action Input -> 执行工具 -> Observation -> 下一轮。
  熔断: tool_calls >= MAX_TOOL_CALLS 时返回 truncated 状态。
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.utils.tokenizer import count_messages_tokens, estimate_tokens

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5


@dataclass
class DeepAgentResult:
    task_id: str
    goal: str
    status: str = "pending"
    steps: list[dict] = field(default_factory=list)
    report: str = ""
    error: str = ""
    total_elapsed_ms: int = 0
    token_consumed: int = 0
    token_budget: int = 500_000
    termination_reason: str = ""


def _build_react_system_prompt(goal: str, tenant_id: int) -> str:
    return (
        f"你是智枢ZhiHub的DeepAgent，一个自主规划执行的分析助手。\n\n"
        f"任务目标: {goal}\n\n"
        "你可以使用以下工具:\n\n"
        "1. query_stats -- 查询工单和订单统计（总数、处理中等），无需参数\n"
        "2. search_sop -- 检索知识库SOP文档，参数: {\"query\": \"搜索词\"}\n"
        "3. finish -- 完成任务并提交报告，参数: {\"report\": \"报告内容\"}\n\n"
        "请按以下格式思考和行动:\n\n"
        "Thought: 思考下一步\n"
        "Action: 工具名称\n"
        "Action Input: JSON参数\n\n"
        f"最多调用工具 {MAX_TOOL_CALLS} 次。报告含: 数据概览/关键发现/改进建议/后续行动，300-500字。\n"
    )


def _parse_react_response(text: str) -> tuple[str, str, str]:
    thought = ""
    action = ""
    action_input = "{}"

    m = re.search(r"Thought:\s*(.+?)(?=\nAction:|$)", text, re.DOTALL)
    if m:
        thought = m.group(1).strip()

    m = re.search(r"Action:\s*(\w+)", text)
    if m:
        action = m.group(1).strip()

    m = re.search(r"Action Input:\s*(.*?)(?=\nThought:|$)", text, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        parsed = _extract_json_object(raw)
        action_input = json.dumps(parsed, ensure_ascii=False) if parsed else raw[:500]

    return thought, action, action_input


def _extract_json_object(text: str) -> dict:
    """从文本中提取第一个括号平衡的 JSON 对象（兼容报告内容含 } 的场景）。失败返回空 dict。"""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def _execute_tool(action: str, action_input: str, goal: str, tenant_id: int) -> str:
    try:
        params = json.loads(action_input) if action_input else {}
    except json.JSONDecodeError:
        params = {}

    if action == "query_stats":
        return json.dumps(_query_stats(tenant_id), ensure_ascii=False)
    elif action == "search_sop":
        query = params.get("query", goal)
        docs = _search_sop(query, tenant_id)
        return json.dumps({"docs_found": len(docs), "docs": docs[:3]}, ensure_ascii=False)
    elif action == "finish":
        return params.get("report", "")
    else:
        return f"未知工具: {action}"


def _response_token_usage(response, messages: list[dict], output: str) -> int:
    """优先读取模型网关的真实 usage；缺失时用本地估算，保证所有调用均计入预算。"""
    usage = getattr(response, "usage", None)
    if usage is not None:
        if isinstance(usage, dict):
            total = usage.get("total_tokens")
        else:
            total = getattr(usage, "total_tokens", None)
        if isinstance(total, (int, float)) and total >= 0:
            return int(total)
    return count_messages_tokens(messages) + estimate_tokens(output)


def _mark_limit_failure(result: DeepAgentResult, reason: str, message: str) -> None:
    """统一记录资源限制终止，避免超限后继续查询或调用模型。"""
    result.status = "failed"
    result.termination_reason = reason
    result.error = f"资源超限失败：{message}"


def execute(
    task_id: str,
    goal: str,
    tenant_id: int,
    timeout_seconds: int | None = None,
    token_budget: int | None = None,
) -> DeepAgentResult:
    """ReAct 自主规划循环，并强制执行 token 与墙钟时间双重预算。"""
    from app.config import settings

    start = time.perf_counter()
    effective_timeout = (
        settings.deep_agent_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    effective_budget = (
        settings.deep_agent_token_budget if token_budget is None else token_budget
    )
    result = DeepAgentResult(
        task_id=task_id,
        goal=goal,
        status="running",
        token_budget=max(0, int(effective_budget)),
    )
    tool_calls = 0
    deadline = start + max(0, effective_timeout)

    if effective_timeout <= 0:
        _mark_limit_failure(result, "timeout", f"执行时间达到 {effective_timeout} 秒上限")
        result.total_elapsed_ms = int((time.perf_counter() - start) * 1000)
        return result
    if effective_budget <= 0:
        _mark_limit_failure(result, "token_budget", f"token 消耗达到 {effective_budget} 上限")
        result.total_elapsed_ms = int((time.perf_counter() - start) * 1000)
        return result

    # 占位 key 检测: LLM 未配置时快速降级，避免测试/开发环境挂起
    if not settings.deepseek_api_key or "placeholder" in settings.deepseek_api_key:
        logger.warning("DeepSeek API key is placeholder/empty, DeepAgent falling back to template report")
        # 直接生成模板报告（不调 LLM，避免占位 key 触发 60s 超时挂起）
        result.report = (
            f"# {goal}\n\n"
            f"## 数据概览\n"
            f"- 总工单数: N/A（LLM 未配置，未查询实时数据）\n"
            f"- 处理中: N/A\n\n"
            f"## 说明\n"
            f"（DeepSeek API key 未配置，使用模板化摘要。配置真实 key 后自动启用 ReAct 自主规划。）\n"
        )
        result.status = "completed"
        result.steps.append({
            "step": 1,
            "label": "降级模板（LLM 未配置）",
            "thought": "LLM API key 未配置，跳过 ReAct 循环，使用模板生成报告",
            "action": "fallback",
            "action_input": "{}",
            "observation": "LLM 未配置，使用降级模板",
            "tool_calls": 1,
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        })
        result.total_elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(f"DeepAgent {task_id}: {result.status} (fallback) in {result.total_elapsed_ms}ms")
        return result

    system_prompt = _build_react_system_prompt(goal, tenant_id)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请开始执行任务: {goal}"},
    ]

    try:
        from app.core.llm_client import llm_complete

        while tool_calls < MAX_TOOL_CALLS:
            # PRD §3.11：默认 10 分钟墙钟上限；超限后不得再调用模型或工具。
            remaining_seconds = deadline - time.perf_counter()
            if remaining_seconds <= 0:
                _mark_limit_failure(
                    result, "timeout", f"执行时间达到 {effective_timeout} 秒上限"
                )
                break

            remaining_tokens = result.token_budget - result.token_consumed
            prompt_tokens = count_messages_tokens(messages)
            if remaining_tokens <= prompt_tokens:
                _mark_limit_failure(
                    result,
                    "token_budget",
                    f"token 消耗达到 {result.token_budget} 上限",
                )
                break

            step_start = time.perf_counter()
            resp = llm_complete(
                messages=messages,
                max_tokens=min(500, max(1, remaining_tokens - prompt_tokens)),
                temperature=0.0,
                timeout=max(0.1, min(60.0, remaining_seconds)),
            )
            llm_output = resp.choices[0].message.content.strip()
            result.token_consumed += _response_token_usage(resp, messages, llm_output)

            if result.token_consumed >= result.token_budget:
                _mark_limit_failure(
                    result,
                    "token_budget",
                    f"token 消耗达到 {result.token_consumed}/{result.token_budget}",
                )
                break
            if time.perf_counter() >= deadline:
                _mark_limit_failure(
                    result, "timeout", f"执行时间达到 {effective_timeout} 秒上限"
                )
                break

            thought, action, action_input = _parse_react_response(llm_output)
            observation = _execute_tool(action, action_input, goal, tenant_id)
            tool_calls += 1

            result.steps.append({
                "step": len(result.steps) + 1,
                "label": f"ReAct: {action}",
                "thought": thought,
                "action": action,
                "action_input": action_input,
                "observation": observation[:500],
                "tool_calls": tool_calls,
                "elapsed_ms": int((time.perf_counter() - step_start) * 1000),
            })

            if time.perf_counter() >= deadline:
                _mark_limit_failure(
                    result, "timeout", f"执行时间达到 {effective_timeout} 秒上限"
                )
                break

            if action == "finish":
                result.report = observation
                result.status = "completed"
                break

            messages.append({"role": "assistant", "content": llm_output})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
        else:
            result.status = "truncated"
            result.termination_reason = "tool_limit"
            result.error = f"工具调用达上限({MAX_TOOL_CALLS})，任务熔断"

    except Exception as e:
        logger.exception(f"DeepAgent {task_id} failed: {e}")
        result.status = "failed"
        result.error = str(e)
        result.termination_reason = "execution_error"

    result.total_elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(f"DeepAgent {task_id}: {result.status} in {result.total_elapsed_ms}ms")
    return result


def _query_stats(tenant_id: int) -> dict:
    try:
        from app.core.database import SessionLocal
        from app.models.ticket import Ticket
        from sqlalchemy import func, select

        db = SessionLocal()
        try:
            total = db.execute(
                select(func.count()).select_from(Ticket).where(Ticket.tenant_id == tenant_id)
            ).scalar() or 0
            open_count = db.execute(
                select(func.count()).select_from(Ticket).where(
                    Ticket.tenant_id == tenant_id, Ticket.status.in_(["open", "in_progress"])
                )
            ).scalar() or 0
            return {"total_tickets": total, "open_tickets": open_count, "source": "MySQL"}
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Stats query failed: {e}")
        return {"total_tickets": None, "open_tickets": None, "source": "unavailable"}


def _search_sop(goal: str, tenant_id: int) -> list[str]:
    try:
        from app.services.retrieval_service import hybrid_search
        result = hybrid_search(query=goal, tenant_id=tenant_id, history=[])
        docs = [r.chunk_text for r in result.hybrid_results[:5]]
        return docs if docs else ["（知识库为空，无SOP文档）"]
    except Exception as e:
        logger.warning(f"SOP search failed: {e}")
        return ["SOP 检索暂不可用（建议先上传知识库文档）"]


def _generate_report(goal: str, stats: dict, sop_docs: list[str]) -> str:
    unavailable = stats.get("source") == "unavailable"
    stats_note = "统计不可用，下面没有真实工单数量。\n" if unavailable else ""
    total = "不可用" if unavailable else stats.get("total_tickets", "N/A")
    open_count = "不可用" if unavailable else stats.get("open_tickets", "N/A")
    prompt = (
        "你是智枢ZhiHub的运营分析助手。请根据以下数据生成一份中文分析报告。\n\n"
        f"任务目标：{goal}\n\n"
        f"{stats_note}"
        f"统计数据：\n  - 总工单数：{total}\n"
        f"  - 处理中工单：{open_count}\n\n"
        f"相关知识库SOP片段：\n"
    )
    for i, doc in enumerate(sop_docs[:3], 1):
        prompt += f"  [{i}] {doc[:300]}\n"
    prompt += "\n请生成包含以下部分的分析报告：\n1. 数据概览\n2. 关键发现\n3. 改进建议\n4. 后续行动\n\n报告字数：300-500字。"

    try:
        from app.core.llm_client import llm_complete
        resp = llm_complete(messages=[{"role": "user", "content": prompt}], max_tokens=1000, temperature=0.3)
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        unavailable = "统计不可用，以下不是真实工单数量。\n" if stats.get("source") == "unavailable" else ""
        total = "不可用" if stats.get("source") == "unavailable" else stats.get("total_tickets", "N/A")
        open_count = "不可用" if stats.get("source") == "unavailable" else stats.get("open_tickets", "N/A")
        return (
            f"# {goal}\n\n## 数据概览\n{unavailable}- 总工单数: {total}\n"
            f"- 处理中: {open_count}\n\n"
            f"## 说明\n（报告生成遇到技术问题，以上为模板化摘要。）\n"
        )
