"""真实 MySQL + Redis 会话状态流转基准（PRD §4.1）。

在同一租户内反复执行 created → waiting_human → human_handling →
agent_handling → resolved → closed，统计各阶段和完整链路 P50/P95/P99。
脚本不调用 LLM，专门度量状态机与持久化开销；结束后清理全部测试数据。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.redis import get_redis
from app.crud import session as session_crud
from app.models.message import Message
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.tenant_member import TenantMember
from app.services import human_service, session_service


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 3)


def _summary(values: list[float]) -> dict:
    return {
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": round(max(values), 3),
    }


def _measure(action) -> tuple[object, float]:
    started = time.perf_counter()
    result = action()
    return result, (time.perf_counter() - started) * 1000


def run(*, tenant_id: int, iterations: int, target_ms: float) -> dict:
    db = SessionLocal()
    redis_client = get_redis()
    marker = f"transition-bench-{uuid.uuid4().hex[:10]}"
    session_ids: list[int] = []
    timings: dict[str, list[float]] = {
        "session_created": [],
        "handoff_states": [],
        "human_claimed": [],
        "returned_to_agent": [],
        "resolved_and_closed": [],
        "full_state_chain": [],
    }
    try:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None or tenant.status != "active":
            raise RuntimeError(f"租户 {tenant_id} 不存在或非 active")
        agent_id = db.execute(
            select(TenantMember.user_id)
            .where(
                TenantMember.tenant_id == tenant_id,
                TenantMember.role.in_(["agent", "admin"]),
            )
            .order_by(TenantMember.role.asc())
            .limit(1)
        ).scalar_one_or_none()
        if agent_id is None:
            raise RuntimeError(f"租户 {tenant_id} 没有可用于认领的坐席")

        for index in range(iterations):
            chain_started = time.perf_counter()
            created, elapsed = _measure(
                lambda: session_service.create_session(
                    db,
                    tenant_id=tenant_id,
                    end_user_id=f"{marker}-{index}",
                    channel="benchmark",
                )
            )
            session_id = created["session_id"]
            session_ids.append(session_id)
            timings["session_created"].append(elapsed)

            item = session_crud.get_by_id(db, session_id, tenant_id)

            def handoff_states():
                for status in ("routing", "agent_handling", "waiting_human"):
                    session_crud.update_status(db, item, status)
                    db.commit()

            _, elapsed = _measure(handoff_states)
            timings["handoff_states"].append(elapsed)

            _, elapsed = _measure(
                lambda: human_service.claim_session(
                    db, session_id=session_id, agent_id=agent_id, tenant_id=tenant_id
                )
            )
            timings["human_claimed"].append(elapsed)

            _, elapsed = _measure(
                lambda: human_service.return_to_agent(
                    db, session_id=session_id, agent_id=agent_id, tenant_id=tenant_id
                )
            )
            timings["returned_to_agent"].append(elapsed)

            def close_session():
                session_crud.update_status(db, item, "resolved")
                db.commit()
                session_crud.update_status(db, item, "closed")
                db.commit()

            _, elapsed = _measure(close_session)
            timings["resolved_and_closed"].append(elapsed)
            timings["full_state_chain"].append((time.perf_counter() - chain_started) * 1000)

        metrics = {name: _summary(values) for name, values in timings.items()}
        passed = metrics["full_state_chain"]["p99_ms"] < target_ms
        return {
            "marker": marker,
            "tenant_id": tenant_id,
            "iterations": iterations,
            "target_ms": target_ms,
            "passed": passed,
            "metrics": metrics,
        }
    finally:
        if session_ids:
            db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
            db.execute(delete(Session).where(Session.id.in_(session_ids)))
            db.commit()
            for session_id in session_ids:
                redis_client.delete(
                    f"session:context:{session_id}",
                    f"claim:session:{session_id}",
                )
            try:
                from pymongo import MongoClient
                from app.config import settings

                mongo = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
                mongo.zhihub.analytics_events.delete_many(
                    {"properties.session_id": {"$in": session_ids}}
                )
                mongo.close()
            except Exception:
                pass
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ZhiHub session state transition benchmark")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--target-ms", type=float, default=200.0)
    args = parser.parse_args()
    if args.iterations < 10:
        parser.error("--iterations must be at least 10")

    report = run(
        tenant_id=args.tenant_id,
        iterations=args.iterations,
        target_ms=args.target_ms,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
