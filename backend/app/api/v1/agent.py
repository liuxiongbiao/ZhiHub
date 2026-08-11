"""
backend/app/api/v1/agent.py

职责: DeepAgent 任务发起与状态查询
对应日程: Day 8

端点概览:
  POST /deep-task        发起 DeepAgent 任务（同步执行，返回完整结果；仅 admin/agent 角色）
  GET  /deep-task/{id}   查询任务状态/结果（按租户归属隔离）

安全设计:
  - 角色门禁: POST 仅 admin/agent（DeepAgent 是运营后台功能，user 角色无权触发）
  - 租户隔离: 任务存储带 tenant_id，GET 校验归属，不匹配返回 404（防任务存在性枚举）
  - TTL 清理: 任务保留 24h，读写时惰性清理过期项，防内存无限增长

注意: demo 用内存 dict 存储任务，生产应换 MongoDB / Redis（TTL 由存储层原生支持）。

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/agent
  - 下游: app.agents.deep_agent.planner.execute
"""

import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_role

router = APIRouter()

# 内存存储（demo 简化，生产用 MongoDB/Redis）；TTL 惰性清理防长期运行内存无限增长
_task_store: dict[str, dict] = {}
_TASK_TTL_SECONDS = 24 * 60 * 60  # 任务保留 24h


def _now_naive_utc() -> datetime:
    """naive UTC 当前时间（项目约定: 统一 naive UTC 兼容 DB）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _prune_tasks() -> None:
    """惰性清理过期任务（每次读写时调用）。

    demo 内存存储的兜底，避免长期运行 _task_store 无限增长；
    生产换 Redis 后应依赖原生 TTL 而非此函数。
    """
    now = _now_naive_utc()
    for task_id in [
        tid for tid, task in _task_store.items()
        if now - task.get("created_at", now) > timedelta(seconds=_TASK_TTL_SECONDS)
    ]:
        del _task_store[task_id]


class DeepTaskRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=500, description="任务目标，如'生成本周工单积压分析报告'")


@router.post("/deep-task", dependencies=[Depends(require_role("admin", "agent"))])
def create_deep_task(body: DeepTaskRequest, request: Request):
    """发起 DeepAgent 任务: 调 planner.execute 同步执行并存储结果。仅 admin/agent 角色。"""
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="未认证")
    _prune_tasks()
    task_id = f"deep-{uuid.uuid4().hex[:12]}"
    now = _now_naive_utc()

    # 保存初始状态（含租户归属，供 GET 校验；pending 时 GET 可看到"处理中"而非 404）
    _task_store[task_id] = {
        "task_id": task_id, "goal": body.goal, "status": "pending",
        "tenant_id": tenant_id, "created_at": now,
    }

    # 同步执行（demo 简化，生产用 Celery 异步）
    from app.agents.deep_agent.planner import execute

    result = execute(task_id=task_id, goal=body.goal, tenant_id=tenant_id)

    # 更新结果
    _task_store[task_id] = {
        "task_id": task_id,
        "goal": body.goal,
        "status": result.status,
        "steps": [{"step": s["step"], "label": s["label"], "result": s["result"], "elapsed_ms": s["elapsed_ms"]} for s in result.steps],
        "report": result.report,
        "error": result.error,
        "total_elapsed_ms": result.total_elapsed_ms,
        "tenant_id": tenant_id,
        "created_at": now,
    }

    return _task_store[task_id]


@router.get("/deep-task/{task_id}")
def get_deep_task(task_id: str, request: Request):
    """查询 DeepAgent 任务状态/结果。

    租户归属不匹配同样返回 404（防任务存在性枚举，与"统一报错防探测"风格一致）。
    """
    _prune_tasks()
    task = _task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None or task.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
