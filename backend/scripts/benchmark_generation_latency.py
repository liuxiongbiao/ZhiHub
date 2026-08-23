"""真实知识问答流式链路延迟基准：记录首包、完整回答与收尾时间。

建议在已启用真实 LLM/Embedding 的后端容器内执行：
    python -m scripts.benchmark_generation_latency --rounds 25

脚本为每轮创建独立会话，调用正式 SSE API，并在结束后清理基准会话、消息、
Redis 上下文和消息限流键。不会输出密钥或完整令牌。
"""

import argparse
import json
import statistics
import time

import httpx

from app.core.database import SessionLocal
from app.core.redis import get_redis
from app.core.security import create_access_token
from app.models.message import Message
from app.models.session import Session
from app.models.tenant_member import TenantMember


def _percentile(values: list[float], percentile: float) -> float:
    """按最近秩算法计算分位值，避免少量样本外推。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile / 100)))
    return ordered[index]


def _create_session(tenant_id: int, index: int) -> int:
    """直接创建一条独立基准会话，避免把创建接口耗时混入生成指标。"""
    db = SessionLocal()
    try:
        session = Session(
            tenant_id=tenant_id,
            status="created",
            end_user_id=None,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        print(f"round {index}: session={session.id}", flush=True)
        return session.id
    finally:
        db.close()


def _cleanup_sessions(session_ids: list[int], user_id: int) -> None:
    """清理基准数据，确保脚本可重复执行且不污染运营统计。"""
    redis = get_redis()
    for session_id in session_ids:
        redis.delete(f"session:context:{session_id}")
    redis.delete(f"rate:msg:{user_id}:minute")
    redis.delete(f"rate:user:{user_id}:minute")

    db = SessionLocal()
    try:
        db.query(Message).filter(Message.session_id.in_(session_ids)).delete(
            synchronize_session=False
        )
        db.query(Session).filter(Session.id.in_(session_ids)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _select_admin(tenant_id: int) -> tuple[int, str]:
    """选择租户管理员并签发仅供本机基准使用的短期令牌。"""
    db = SessionLocal()
    try:
        member = (
            db.query(TenantMember)
            .filter_by(tenant_id=tenant_id, role="admin")
            .order_by(TenantMember.id.asc())
            .first()
        )
        if member is None:
            raise RuntimeError(f"租户 {tenant_id} 没有管理员，无法运行基准")
        token = create_access_token(
            subject=str(member.user_id),
            extra_claims={"tenant_id": tenant_id, "role": "admin"},
            expires_minutes=30,
        )
        return member.user_id, token
    finally:
        db.close()


def _run_once(
    client: httpx.Client,
    *,
    base_url: str,
    token: str,
    session_id: int,
    question: str,
) -> tuple[float, float, float, int]:
    """消费一轮 SSE，返回首包、最后文本块、DONE 时间和文本字符数。"""
    start = time.perf_counter()
    first_chunk_at: float | None = None
    last_chunk_at: float | None = None
    done_at: float | None = None
    answer_chars = 0
    with client.stream(
        "POST",
        f"{base_url}/api/v1/session/message/stream",
        headers={
            "Authorization": f"Bearer {token}",
            "X-CSRF-Token": token,
        },
        json={"session_id": session_id, "content": question},
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            now = time.perf_counter()
            if payload == "[DONE]":
                done_at = now
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                raise RuntimeError(event["error"])
            chunk = event.get("chunk")
            if chunk:
                first_chunk_at = first_chunk_at or now
                last_chunk_at = now
                answer_chars += len(chunk)

    if first_chunk_at is None or last_chunk_at is None:
        raise RuntimeError(f"会话 {session_id} 未收到回答文本块")
    done_at = done_at or last_chunk_at
    return (
        first_chunk_at - start,
        last_chunk_at - start,
        done_at - start,
        answer_chars,
    )


def main() -> None:
    """解析参数、执行预热和正式样本，并打印可复核的分位结果。"""
    parser = argparse.ArgumentParser(description="ZhiHub 真实生成延迟基准")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=25)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--question", default="登录页面一直转圈怎么办？")
    args = parser.parse_args()

    user_id, token = _select_admin(args.tenant_id)
    session_ids: list[int] = []
    ttfb_values: list[float] = []
    answer_values: list[float] = []
    done_values: list[float] = []
    total_runs = args.warmup + args.rounds

    try:
        with httpx.Client(timeout=httpx.Timeout(120.0)) as client:
            for index in range(1, total_runs + 1):
                session_id = _create_session(args.tenant_id, index)
                session_ids.append(session_id)
                ttfb, answer, done, chars = _run_once(
                    client,
                    base_url=args.base_url.rstrip("/"),
                    token=token,
                    session_id=session_id,
                    question=args.question,
                )
                phase = "warmup" if index <= args.warmup else "sample"
                print(
                    f"  {phase}: TTFB={ttfb:.3f}s answer={answer:.3f}s "
                    f"done={done:.3f}s chars={chars}",
                    flush=True,
                )
                if index > args.warmup:
                    ttfb_values.append(ttfb)
                    answer_values.append(answer)
                    done_values.append(done)
                # 每轮删除基准用户的限流键；仍经过真实判断，但后续样本不会被累计次数拒绝。
                get_redis().delete(f"rate:msg:{user_id}:minute")
                get_redis().delete(f"rate:user:{user_id}:minute")
    finally:
        if session_ids:
            _cleanup_sessions(session_ids, user_id)

    print("\n生成延迟基准结果")
    print(f"  环境: {args.base_url} | 租户: {args.tenant_id} | 样本: {args.rounds}")
    for label, values in (
        ("TTFB", ttfb_values),
        ("完整回答", answer_values),
        ("DONE", done_values),
    ):
        print(
            f"  {label}: P50={_percentile(values, 50):.3f}s "
            f"P95={_percentile(values, 95):.3f}s "
            f"P99={_percentile(values, 99):.3f}s "
            f"Mean={statistics.mean(values):.3f}s"
        )


if __name__ == "__main__":
    main()
