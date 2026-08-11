"""
backend/app/agents/deep_agent/planner.py

职责: DeepAgent 自主规划器--接收自然语言任务目标 -> 自主分解执行计划 -> 逐步执行
      (查 MySQL 统计 -> 检索知识库 SOP -> 综合生成报告) -> 工具调用次数熔断。
分工: 🔴 强模型独立完成（自主规划步骤设计+资源熔断是 DeepAgent vs LangGraph 对比的核心论据）
对应日程: Day 8

核心设计:
  DeepAgent 是与 LangGraph 多智能体编排对比的另一种范式--自主规划执行而非预定义状态图。
  这里做最小实现：3 步固定骨架（查询统计 -> 检索 SOP -> 生成报告），不做通用规划器；
  资源熔断只做"最多调用工具 5 次"这一条，不做完整 token 预算跟踪。每步前检查熔断，
  达上限返回 truncated 状态。中间产出写 result.steps 供 trace。

关键技术点:
  - 3 步固定骨架: 查询统计(Step1) -> 检索 SOP(Step2) -> 生成报告(Step3)，简化版自主规划
  - MAX_TOOL_CALLS 熔断: 每步前检查 tool_calls >= 5 则 truncated，防失控
  - task_store 内存存储: result 内存累积，demo 不持久化
  - 降级策略: 统计查询失败用 mock 数据，SOP 检索失败返回提示，报告生成失败返回模板

依赖关系:
  - 上游: app/api/v1/agent.py（DeepAgent 任务触发）、tests/unit/test_deep_agent.py
  - 下游: app/core/database.py（统计查询）、app/services/retrieval_service.py（SOP 检索）、litellm（报告）

简化: 最小实现3步固定骨架（查询统计->检索SOP->生成报告），不做通用规划器；
      资源熔断只做"最多调用工具5次"这一条，不做完整token预算跟踪。
"""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 最大工具调用次数熔断--DeepAgent 防失控的核心，达到即 truncated
MAX_TOOL_CALLS = 5


@dataclass
class DeepAgentResult:
    """DeepAgent 执行结果。

    status: pending/running/completed/failed/truncated
    steps: 每步执行记录（含 action/result/tool_calls/elapsed_ms）
    report: 最终报告（Step3 产出）
    """
    task_id: str
    goal: str
    status: str = "pending"  # pending / running / completed / failed
    steps: list[dict] = field(default_factory=list)
    report: str = ""
    error: str = ""
    total_elapsed_ms: int = 0


def execute(task_id: str, goal: str, tenant_id: int) -> DeepAgentResult:
    """执行 DeepAgent 任务。

    流程:
      1. 初始化 result（status=running）
      2. _run_steps 执行 3 步固定骨架，每步前检查熔断
      3. truncated -> 记熔断错误；completed -> 报告已生成
      4. 异常 -> status=failed 记错误
      5. 记总耗时返回

    设计决策:
      3 步固定骨架而非通用规划器，是 demo 简化--真实 DeepAgent 应让 LLM 自主分解任务并选工具，
      但那需要 ReAct 循环 + 工具注册表，复杂度高。固定骨架足以讲清"自主规划执行"范式与
      LangGraph"预定义状态图"的对比。熔断用 tool_calls 计数而非 token 预算，简化实现。

    固定3步流程:
      1. 查询统计 -> 2. 检索SOP -> 3. 生成报告

    Args:
        task_id: 任务唯一ID
        goal: 自然语言任务目标（如"生成本周工单积压分析报告"）
        tenant_id: 租户ID
    Returns:
        DeepAgentResult（含完整执行步骤和最终报告）
    """
    start = time.perf_counter()
    result = DeepAgentResult(task_id=task_id, goal=goal, status="running")
    tool_calls = 0

    def _run_steps() -> str:
        """执行 3 步，每步前检查工具调用熔断。返回最终状态。"""
        nonlocal tool_calls

        # ─── Step 1: 查询 MySQL 统计 ───
        if tool_calls >= MAX_TOOL_CALLS:
            return "truncated"
        step1_start = time.perf_counter()
        stats = _query_stats(tenant_id)
        tool_calls += 1
        result.steps.append({
            "step": 1, "label": "查询统计",
            "action": "SELECT COUNT FROM tickets/orders",
            "result": stats,
            "tool_calls": tool_calls,
            "elapsed_ms": int((time.perf_counter() - step1_start) * 1000),
        })

        # ─── Step 2: 检索知识库 SOP ───
        if tool_calls >= MAX_TOOL_CALLS:
            return "truncated"
        step2_start = time.perf_counter()
        sop_docs = _search_sop(goal, tenant_id)
        tool_calls += 1
        result.steps.append({
            "step": 2, "label": "检索SOP",
            "action": "调用混合检索获取相关处理指南",
            "result": {"docs_found": len(sop_docs), "preview": sop_docs[:2]},
            "tool_calls": tool_calls,
            "elapsed_ms": int((time.perf_counter() - step2_start) * 1000),
        })

        # ─── Step 3: LLM 生成报告 ───
        if tool_calls >= MAX_TOOL_CALLS:
            return "truncated"
        step3_start = time.perf_counter()
        report = _generate_report(goal, stats, sop_docs)
        tool_calls += 1
        result.steps.append({
            "step": 3, "label": "生成报告",
            "action": "LLM综合统计+SOP生成分析报告",
            "result": {"report_length": len(report)},
            "tool_calls": tool_calls,
            "elapsed_ms": int((time.perf_counter() - step3_start) * 1000),
        })
        result.report = report
        return "completed"

    try:
        result.status = _run_steps()
        if result.status == "truncated":
            result.error = f"工具调用达上限({MAX_TOOL_CALLS})，任务熔断"
        result.total_elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(f"DeepAgent {task_id}: {result.status} in {result.total_elapsed_ms}ms, {len(result.report)} chars")

    except Exception as e:
        logger.exception(f"DeepAgent {task_id} failed: {e}")
        result.status = "failed"
        result.error = str(e)

    return result


def _query_stats(tenant_id: int) -> dict:
    """Step 1: 查询 MySQL 统计本周工单/订单概况。

    流程:
      1. 查 Ticket 表总工单数 + 处理中工单数（按 tenant_id 过滤）
      2. 失败 -> 降级返回 mock 数据

    设计决策:
      失败降级用 mock 数据而非抛异常，保证 DeepAgent 链路不因 DB 不可用中断--
      报告里会标注数据来源是 mock 还是真实，用户可知晓数据可信度。

    Demo 简化: 返回 mock 统计数据，生产环境需真实 SQL 聚合。
    """
    try:
        from app.core.database import SessionLocal
        from app.models.ticket import Ticket
        from sqlalchemy import func, select

        db = SessionLocal()
        try:
            total_tickets = db.execute(
                select(func.count()).select_from(Ticket).where(Ticket.tenant_id == tenant_id)
            ).scalar() or 0

            open_tickets = db.execute(
                select(func.count()).select_from(Ticket).where(
                    Ticket.tenant_id == tenant_id, Ticket.status.in_(["open", "in_progress"])
                )
            ).scalar() or 0

            return {
                "total_tickets": total_tickets,
                "open_tickets": open_tickets,
                "source": "MySQL tickets表",
            }
        finally:
            db.close()
    except Exception as e:
        # 降级：MySQL 不可用时用 mock 数据，标注来源
        logger.warning(f"Stats query failed (using mock): {e}")
        return {
            "total_tickets": 25,
            "open_tickets": 8,
            "source": "mock（MySQL不可用时的降级数据）",
        }


def _search_sop(goal: str, tenant_id: int) -> list[str]:
    """Step 2: 调用混合检索获取相关 SOP/处理指南。

    流程:
      1. hybrid_search 检索与任务目标相关的文档片段
      2. 取 Top5 chunk_text
      3. 失败/空 -> 返回提示文案

    设计决策:
      复用 retrieval_service 的混合检索，保证 DeepAgent 与知识 Agent 用同一套检索能力。
      空结果返回提示而非空列表，让报告生成步骤有内容可写（提示用户上传知识库）。

    从知识库中检索与任务目标相关的文档片段。
    """
    try:
        from app.services.retrieval_service import hybrid_search

        result = hybrid_search(query=goal, tenant_id=tenant_id, history=[])
        docs = [r.chunk_text for r in result.hybrid_results[:5]]
        return docs if docs else ["（知识库为空，无SOP文档）"]
    except Exception as e:
        logger.warning(f"SOP search failed: {e}")
        # T25: 异常文本不进用户可见报告（会泄漏内部路径/连接串），只留日志
        return ["SOP 检索暂不可用（建议先上传知识库文档）"]


def _generate_report(goal: str, stats: dict, sop_docs: list[str]) -> str:
    """Step 3: LLM 综合统计 + SOP 生成分析报告。

    流程:
      1. 拼 prompt（任务目标 + 统计数据 + SOP 片段 + 报告结构要求）
      2. litellm completion（temperature=0.3，max_tokens=1000）
      3. 失败 -> 降级返回结构化模板

    设计决策:
      prompt 明确要求报告含 4 部分（数据概览/关键发现/改进建议/后续行动）并限字数 300-500，
      引导 LLM 产出结构化、可控长度的报告。temperature=0.3 保留灵活性但控制幻觉。
      失败降级返回模板化摘要，标注"技术问题"让用户知晓报告非 LLM 生成。

    使用 litellm 调用 DeepSeek，temperature=0.3 保留灵活性但控制幻觉。
    """
    prompt = (
        "你是智枢ZhiHub的运营分析助手。请根据以下数据生成一份中文分析报告。\n\n"
        f"任务目标：{goal}\n\n"
        f"统计数据：\n"
        f"  - 总工单数：{stats.get('total_tickets', 'N/A')}\n"
        f"  - 处理中工单：{stats.get('open_tickets', 'N/A')}\n"
        f"  - 数据来源：{stats.get('source', 'N/A')}\n\n"
        f"相关知识库SOP片段：\n"
    )
    for i, doc in enumerate(sop_docs[:3], 1):
        prompt += f"  [{i}] {doc[:300]}\n"

    prompt += (
        "\n请生成包含以下部分的分析报告：\n"
        "1. 数据概览\n"
        "2. 关键发现\n"
        "3. 改进建议（基于SOP）\n"
        "4. 后续行动\n\n"
        "报告字数：300-500字。"
    )

    try:
        from app.core.llm_client import llm_complete

        resp = llm_complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.3,
        )
        report = resp.choices[0].message.content.strip()
        return report
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        # 降级：返回结构化模板（标注技术问题，让用户知晓非 LLM 生成）
        return (
            f"# {goal}\n\n"
            f"## 数据概览\n"
            f"- 总工单数: {stats.get('total_tickets', 'N/A')}\n"
            f"- 处理中: {stats.get('open_tickets', 'N/A')}\n\n"
            f"## 说明\n"
            f"（报告生成遇到技术问题，以上为自动生成的模板化摘要。请检查LLM API配置后重试。）\n"
        )
