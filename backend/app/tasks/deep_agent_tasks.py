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


def _public_step(step: dict) -> dict:
    """把规划步骤收成任务结果里的公开字段。有观察时，结果取观察的前 500 字。"""
    item = {
        "step": step.get("step"),
        "label": step.get("label", ""),
        "elapsed_ms": step.get("elapsed_ms", 0),
    }
    for field in ("thought", "action", "action_input", "observation"):
        if field in step:
            item[field] = step[field]
    if "observation" in step:
        item["result"] = step["observation"][:500]
    else:
        item["result"] = step.get("result", "")
    return item


def _notify_deep_agent_finished(task_id: str, goal: str, tenant_id: int, result) -> None:
    """任务结束后写站内信。通知失败不改变任务结果。"""
    try:
        from app.core.database import SessionLocal
        from app.services.notification_service import create_notification

        ndb = SessionLocal()
        try:
            create_notification(
                ndb, tenant_id=tenant_id,
                title=f"DeepAgent 任务{'完成' if result.status == 'completed' else '结束'}",
                content=f"任务目标：{goal[:80]}；状态：{result.status}"
                        + (f"；错误：{result.error[:100]}" if result.error else ""),
                type="deep_agent",
            )
        finally:
            ndb.close()
    except Exception as exc:
        logger.warning(f"DeepAgent notification failed (task={task_id}): {exc}")


def _run_and_save(
    task_id: str,
    goal: str,
    tenant_id: int,
    created_at=None,
    timeout_seconds: int | None = None,
    token_budget: int | None = None,
) -> dict:
    """执行 DeepAgent 任务并保存结果（被 Celery 任务和同步 fallback 共用）。"""
    from app.agents.deep_agent.planner import execute

    result = execute(
        task_id=task_id,
        goal=goal,
        tenant_id=tenant_id,
        timeout_seconds=timeout_seconds,
        token_budget=token_budget,
    )

    final_task = {
        "task_id": task_id,
        "goal": goal,
        "status": result.status,
        "steps": [_public_step(step) for step in result.steps],
        "report": result.report,
        "error": result.error,
        "total_elapsed_ms": result.total_elapsed_ms,
        "token_consumed": result.token_consumed,
        "token_budget": result.token_budget,
        "termination_reason": result.termination_reason,
        "tenant_id": tenant_id,
        "created_at": created_at,
    }
    save_task(final_task)
    _notify_deep_agent_finished(task_id, goal, tenant_id, result)
    return final_task


# Celery 任务：优先尝试，失败时 API 层回退同步执行
try:
    from celery_worker import celery_app

    @celery_app.task(name="app.tasks.deep_agent_tasks.run_deep_agent_task", bind=True, max_retries=1)
    def run_deep_agent_task(self, task_id: str, goal: str, tenant_id: int) -> dict:
        """Celery 任务: 后台执行 DeepAgent ReAct 循环并保存结果。"""
        try:
            from datetime import datetime, timezone
            from app.config import settings
            created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return _run_and_save(
                task_id, goal, tenant_id, created_at=created_at,
                timeout_seconds=settings.deep_agent_timeout_seconds,
                token_budget=settings.deep_agent_token_budget,
            )
        except Exception as e:
            logger.exception(f"DeepAgent task {task_id} failed: {e}")
            self.retry(exc=e, countdown=5)
            return {"task_id": task_id, "status": "failed", "error": str(e)}
except Exception as e:  # pragma: no cover - celery import may fail in minimal env
    logger.warning(f"Celery not available for deep agent tasks: {e}")

    # 占位: 无 Celery 时提供同步执行的兼容接口
    def run_deep_agent_task_sync(task_id: str, goal: str, tenant_id: int) -> dict:
        from datetime import datetime, timezone
        from app.config import settings
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return _run_and_save(
            task_id,
            goal,
            tenant_id,
            created_at=created_at,
            timeout_seconds=settings.deep_agent_timeout_seconds,
            token_budget=settings.deep_agent_token_budget,
        )
