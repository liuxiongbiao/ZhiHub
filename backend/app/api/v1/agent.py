"""
backend/app/api/v1/agent.py

职责: DeepAgent 任务发起与状态查询

端点概览:
  POST /deep-task             发起 DeepAgent 任务（Celery 异步执行；仅 admin/agent 角色）
  GET  /deep-task/{task_id}   查询任务状态/结果（按租户归属隔离）

安全设计:
  - 角色门禁: POST 仅 admin/agent（DeepAgent 是运营后台功能，user 角色无权触发）
  - 租户隔离: 任务存储带 tenant_id，GET 校验归属，不匹配返回 404（防任务存在性枚举）
  - TTL 清理: MongoDB TTL 索引 24h 自动过期；无 MongoDB 时回退内存 dict

P-015: 任务持久化从进程内存 dict 改为 MongoDB，服务重启后历史任务可查询。
      MongoDB 不可用时回退内存 dict（demo/开发零配置兼容）。

异步执行: POST /deep-task 优先通过 Celery 提交后台任务，立即返回 pending 状态；
          Celery/RabbitMQ 不可用时回退同步执行（开发/测试零配置兼容）。
          前端通过 GET /deep-task/{task_id} 轮询获取结果。

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/agent
  - 下游: app.agents.deep_agent.planner.execute、app.core.deep_agent_store
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_role

logger = logging.getLogger(__name__)
router = APIRouter()

# P-015: 内存回退存储（MongoDB 不可用时使用，demo/开发零配置兼容）
_task_store_fallback: dict[str, dict] = {}
_TASK_TTL_SECONDS = 24 * 60 * 60


def _now_naive_utc() -> datetime:
    """naive UTC 当前时间（项目约定: 统一 naive UTC 兼容 DB）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _save_task(task: dict) -> None:
    """保存任务到 MongoDB（失败回退内存）。"""
    try:
        from app.core.deep_agent_store import save_task
        save_task(task)
    except Exception as e:
        logger.warning(f"MongoDB save failed, using in-memory fallback: {e}")
    _task_store_fallback[task["task_id"]] = task
    _prune_fallback()


def _load_task(task_id: str, tenant_id: int) -> dict | None:
    """从 MongoDB 加载任务（失败回退内存）。"""
    # 优先查 MongoDB（权威数据源，Celery worker 结果写入这里）
    try:
        from app.core.deep_agent_store import load_task
        task = load_task(task_id, tenant_id)
        if task:
            return task
    except Exception as e:
        logger.warning(f"MongoDB load failed: {e}")
    # MongoDB 不可用/无记录时回退内存（API 进程内提交的 pending 状态）
    task = _task_store_fallback.get(task_id)
    if task and task.get("tenant_id") == tenant_id:
        return task
    return None


def _prune_fallback() -> None:
    """惰性清理内存回退中的过期任务。"""
    now = _now_naive_utc()
    for task_id in [
        tid for tid, task in _task_store_fallback.items()
        if now - task.get("created_at", now) > timedelta(seconds=_TASK_TTL_SECONDS)
    ]:
        del _task_store_fallback[task_id]


class DeepTaskRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=500, description="任务目标，如'生成本周工单积压分析报告'")


@router.post("/deep-task", dependencies=[Depends(require_role("admin", "agent"))])
def create_deep_task(body: DeepTaskRequest, request: Request):
    """发起 DeepAgent 任务: Celery 异步执行（RabbitMQ 不可用则同步 fallback）。仅 admin/agent 角色。"""
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="未认证")
    task_id = f"deep-{uuid.uuid4().hex[:12]}"
    now = _now_naive_utc()

    # 保存初始状态（含租户归属，供 GET 校验；pending 时 GET 可看到"处理中"而非 404）
    initial_task = {
        "task_id": task_id, "goal": body.goal, "status": "pending",
        "tenant_id": tenant_id, "created_at": now,
    }
    _save_task(initial_task)

    # 优先 Celery 异步提交（RabbitMQ 可用时）
    from app.tasks.deep_agent_tasks import _run_and_save
    try:
        from celery_worker import celery_app
        # 确认 broker 可用：仅尝试连接检查（超时短，避免请求阻塞）
        celery_app.connection().ensure_connection(max_retries=0, timeout=1)
        from app.tasks.deep_agent_tasks import run_deep_agent_task
        run_deep_agent_task.delay(task_id=task_id, goal=body.goal, tenant_id=tenant_id)
        logger.info(f"DeepAgent task {task_id} submitted to Celery")
        return {
            "task_id": task_id,
            "goal": body.goal,
            "status": "pending",
            "steps": [],
            "report": "",
            "error": "",
            "total_elapsed_ms": 0,
            "tenant_id": tenant_id,
            "created_at": now,
        }
    except Exception as e:
        # Celery/RabbitMQ 不可用 -> 同步 fallback（开发/测试零配置兼容）
        logger.warning(f"Celery unavailable ({e}), running DeepAgent synchronously")
        result = _run_and_save(task_id=task_id, goal=body.goal, tenant_id=tenant_id, created_at=now)
        return result


@router.get("/deep-task/{task_id}")
def get_deep_task(task_id: str, request: Request):
    """查询 DeepAgent 任务状态/结果。

    租户归属不匹配同样返回 404（防任务存在性枚举，与"统一报错防探测"风格一致）。
    """
    _prune_fallback()
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _load_task(task_id, tenant_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
