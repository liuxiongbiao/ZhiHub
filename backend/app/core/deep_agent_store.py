"""
backend/app/core/deep_agent_store.py

职责: DeepAgent 任务的 MongoDB 持久化存储（审计修复：替代进程内存 dict，重启不丢失）
设计: 使用 pymongo 同步客户端，与 agent.py 同步端点一致；TTL 24h 自动过期。
"""

import logging
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

_COLLECTION = "deep_agent_tasks"
_TTL_SECONDS = 24 * 3600  # 24h，与原 _TASK_TTL_SECONDS 一致


def _get_collection():
    """获取 MongoDB deep_agent_tasks collection（惰性创建 TTL 索引）。"""
    from pymongo import MongoClient, ASCENDING
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    # pymongo Database 对象不支持 bool()/or 判断，直接使用固定库名（与 settings.mongo_uri 的路径一致）
    db = client["zhihub"]
    col = db[_COLLECTION]
    # 确保 TTL 索引存在（幂等操作）
    col.create_index("created_at", expireAfterSeconds=_TTL_SECONDS)
    col.create_index([("tenant_id", ASCENDING)])
    return col, client


def save_task(task: dict) -> None:
    """保存或更新 DeepAgent 任务到 MongoDB。"""
    col, client = None, None
    try:
        col, client = _get_collection()
        col.update_one(
            {"task_id": task["task_id"]},
            {"$set": task},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"DeepAgent task save failed (non-blocking): {e}")
    finally:
        if client:
            client.close()


def load_task(task_id: str, tenant_id: int) -> dict | None:
    """按 task_id + tenant_id 加载任务（租户隔离）。"""
    client = None
    try:
        col, client = _get_collection()
        task = col.find_one({"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0})
        if task:
            # 兼容上线前已落库的历史任务文档。
            task.setdefault("token_consumed", 0)
            task.setdefault("token_budget", settings.deep_agent_token_budget)
            task.setdefault("termination_reason", "")
        return task
    except Exception as e:
        logger.warning(f"DeepAgent task load failed: {e}")
        return None
    finally:
        if client:
            client.close()
