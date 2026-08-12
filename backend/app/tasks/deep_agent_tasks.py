"""
backend/app/tasks/deep_agent_tasks.py

职责: DeepAgent 任务 Celery 异步执行 -- 将 planner.execute 包装为 Celery 任务，
      由 worker 后台执行，API 层立即返回 pending 状态，前端轮询/查询结果。

设计:
  - 任务执行后结果通过 deep_agent_store 持久化（MongoDB，fallback 内存）
  - Celery broker 不可用时（如开发环境未启动 RabbitMQ），
    API 层有同步 fallback，不阻塞功能
"""

import logging

from app.core.database import SessionLocal
from app.core.deep_agent_store import save_task

logger = logging.getLogger(__name__)


def _run_and_save(task_id: str, goal: str, tenant_id: int, created_at=None) -> dict:
    """执行 DeepAgent 任务并保存结果（被 Celery 任务和同步 fallback 共用）。"""
    from app.agents.deep_agent.planner import execute

    result = execute(task_id=task_id, goal=goal, tenant_id=tenant_id)

    # 构建与 API 层兼容的结果结构（steps 保留 ReAct 字段）
    steps = []
    for s in result.steps:
        step_item = {
            "step": s.get("step"),
            "label": s.get("label", ""),
            "elapsed_ms": s.get("elapsed_ms", 0),
        }
        # ReAct 步骤附加字段（thought/action/action_input/observation）
        # result 字段兼容前端展示：优先取 observation，否则用原始 result
        if "thought" in s:
            step_item["thought"] = s["thought"]
        if "action" in s:
            step_item["action"] = s["action"]
        if "action_input" in s:
            step_item["action_input"] = s["action_input"]
        if "observation" in s:
            step_item["observation"] = s["observation"]
            step_item["result"] = s["observation"][:500]
        else:
            step_item["result"] = s.get("result", "")
        steps.append(step_item)

    final_task = {
        "task_id": task_id,
        "goal": goal,
        "status": result.status,
        "steps": steps,
        "report": result.report,
        "error": result.error,
        "total_elapsed_ms": result.total_elapsed_ms,
        "tenant_id": tenant_id,
        "created_at": created_at,
    }
    save_task(final_task)
    return final_task


# Celery 任务：优先尝试，失败时 API 层回退同步执行
try:
    from celery_worker import celery_app

    @celery_app.task(name="app.tasks.deep_agent_tasks.run_deep_agent_task", bind=True, max_retries=1)
    def run_deep_agent_task(self, task_id: str, goal: str, tenant_id: int) -> dict:
        """Celery 任务: 后台执行 DeepAgent ReAct 循环并保存结果。"""
        try:
            from datetime import datetime, timezone
            created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return _run_and_save(task_id, goal, tenant_id, created_at=created_at)
        except Exception as e:
            logger.exception(f"DeepAgent task {task_id} failed: {e}")
            self.retry(exc=e, countdown=5)
            return {"task_id": task_id, "status": "failed", "error": str(e)}
except Exception as e:  # pragma: no cover - celery import may fail in minimal env
    logger.warning(f"Celery not available for deep agent tasks: {e}")

    # 占位: 无 Celery 时提供同步执行的兼容接口
    def run_deep_agent_task_sync(task_id: str, goal: str, tenant_id: int) -> dict:
        from datetime import datetime, timezone
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return _run_and_save(task_id, goal, tenant_id, created_at=created_at)
