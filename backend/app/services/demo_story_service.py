"""演示故事基线：把已有业务能力组织成可直接讲解的完整客服闭环。

关系库部分只替换固定 demo session 中带 ``demo_seed`` 标记的消息；Mongo
部分只替换同租户的 demo 文档。真实用户交互、审计记录和其他租户数据不受影响。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DbSession

from app.config import settings


DEMO_DEEP_TASK_ID = "deep-demo-weekly-ops"


def demo_widget_api_key(tenant_id: int) -> str:
    """派生仅供本机演示的稳定 Widget Key；生产环境调用会被上层拒绝。"""
    digest = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        f"demo-widget:{tenant_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"zdh_demo_{digest[:32]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _message_scripts() -> dict[str, list[dict]]:
    return {
        "eu-demo-waiting": [
            {"role": "user", "content": "你们支持七天无理由退货吗？退货流程是什么？"},
            {
                "role": "assistant",
                "content": "支持。商品签收后 7 天内且保持完好，可在订单页发起退货；审核后按指引寄回，仓库验收后 1–3 个工作日原路退款。",
                "sources": [{"doc": "七天无理由退货政策.md", "chunk_index": 0, "text": "签收后 7 天内可申请退货"}],
            },
            {"role": "user", "content": "帮我查订单 ORD-DEMO-0003，手机号后四位 2222。"},
            {
                "role": "assistant",
                "content": "订单 ORD-DEMO-0003 金额 899 元，当前已发货，物流单号 SF1234567890。",
            },
            {"role": "user", "content": "商品运输中破损了，你们能额外赔偿我 500 元吗？"},
            {
                "role": "assistant",
                "content": "现有政策无法确认额外赔偿金额，这属于需要人工判断的高风险售后事项。我已为你转接人工客服。",
            },
            {"role": "system", "content": "已进入人工优先队列，预计等待约 4 分钟。"},
        ],
        "eu-demo-closed": [
            {"role": "user", "content": "退款审核通过后多久能到账？"},
            {
                "role": "assistant",
                "content": "支付宝或微信通常 2 小时内到账，银行卡为 1–3 个工作日，对公转账为 3–5 个工作日。",
                "sources": [{"doc": "退款到账时间.md", "chunk_index": 0, "text": "不同渠道到账时间"}],
            },
            {"role": "user", "content": "明白了，谢谢。"},
            {"role": "assistant", "content": "不客气，本次问题已解决。"},
        ],
    }


def _clear_seeded_messages(db: DbSession, session) -> None:
    """先解开评价外键，再删除本会话里由演示剧本写入的消息。"""
    from app.models.csat import CsatEvaluation
    from app.models.message import Message

    seeded_messages = [
        existing
        for existing in db.query(Message).filter_by(session_id=session.id).all()
        if (existing.msg_metadata or {}).get("demo_seed")
    ]
    seeded_message_ids = [item.id for item in seeded_messages]
    if seeded_message_ids:
        db.query(CsatEvaluation).filter(
            CsatEvaluation.message_id.in_(seeded_message_ids)
        ).update({CsatEvaluation.message_id: None}, synchronize_session=False)
        db.flush()
    for existing in seeded_messages:
        db.delete(existing)
    db.flush()


def _insert_script_messages(db: DbSession, session, script: list, now: datetime, end_user_id: str) -> list[int]:
    from app.models.message import Message

    ids: list[int] = []
    start = now - timedelta(minutes=18 if end_user_id == "eu-demo-waiting" else 90)
    for index, item in enumerate(script):
        message = Message(
            session_id=session.id,
            role=item["role"],
            content=item["content"],
            sources=item.get("sources"),
            msg_metadata={"demo_seed": True, "story_step": index + 1},
            created_at=start + timedelta(minutes=index * 2),
            updated_at=start + timedelta(minutes=index * 2),
        )
        db.add(message)
        db.flush()
        ids.append(message.id)
    return ids


def _upsert_closed_csat(db: DbSession, session, message_id: int | None) -> None:
    from app.models.csat import CsatEvaluation

    evaluation = db.query(CsatEvaluation).filter_by(session_id=session.id).first()
    if evaluation is None:
        db.add(CsatEvaluation(
            session_id=session.id,
            message_id=message_id,
            feedback="like",
            score=5,
            comment="回答清晰，到账时间很明确",
        ))
        return
    evaluation.message_id = message_id
    evaluation.feedback = "like"
    evaluation.score = 5
    evaluation.comment = "回答清晰，到账时间很明确"


def _seed_messages(db: DbSession, tenant_id: int, now: datetime) -> tuple[int, dict[str, list[int]]]:
    from app.models.session import Session

    message_ids: dict[str, list[int]] = {}
    total = 0
    for end_user_id, script in _message_scripts().items():
        session = db.query(Session).filter_by(
            tenant_id=tenant_id, end_user_id=end_user_id
        ).first()
        if session is None:
            continue
        _clear_seeded_messages(db, session)
        ids = _insert_script_messages(db, session, script, now, end_user_id)
        total += len(ids)
        message_ids[end_user_id] = ids
        if end_user_id == "eu-demo-closed":
            _upsert_closed_csat(db, session, ids[-1] if ids else None)
    db.commit()
    return total, message_ids


def build_demo_events(now: datetime | None = None) -> list[dict]:
    """构建 30 天稳定趋势；数据被明确标记为 demo，不与真实事件混淆。"""
    now = now or _utcnow()
    events: list[dict] = []
    for offset in range(29, -1, -1):
        day = now - timedelta(days=offset, hours=offset % 5)
        auto = 5 + (offset % 4)
        human = 1 + (1 if offset % 6 == 0 else 0)
        for _ in range(auto):
            events.append({"event_name": "session_resolved", "occurred_at": day, "properties": {"resolution_mode": "auto"}})
        for _ in range(human):
            events.append({"event_name": "session_resolved", "occurred_at": day, "properties": {"resolution_mode": "human"}})
        events.extend([
            {
                "event_name": "retrieval_executed",
                "occurred_at": day + timedelta(minutes=8),
                "properties": {
                    "query": "演示知识咨询",
                    "latency": 420 + (offset % 7) * 24,
                    "vector_top_score": 0.61 + (offset % 3) * 0.01,
                    "hybrid_top_score": 0.78 + (offset % 4) * 0.02,
                    "hyde_fallback": offset % 9 == 0,
                },
            },
            {
                "event_name": "mcp_tool_called",
                "occurred_at": day + timedelta(minutes=12),
                "properties": {"tool_name": "query_order", "success": offset % 17 != 0, "latency": 7 + offset % 5},
            },
        ])
        if offset in {3, 11, 19, 27}:
            events.append({
                "event_name": "refund_approved",
                "occurred_at": day + timedelta(minutes=20),
                "properties": {"amount": 299, "latency_seconds": 1800 + offset * 120},
            })
    events.extend([
        {"event_name": "human_takeover", "occurred_at": now - timedelta(minutes=8), "properties": {"trigger_reason": "敏感赔偿请求", "wait_duration": 0}},
        {"event_name": "security_block", "occurred_at": now - timedelta(hours=7), "properties": {"rule_hit": "资金操作需人工审批"}},
    ])
    return events


def _demo_trace(session_id: int, message_id: int, intent: str, confidence: float, sentiment: str, elapsed_ms: int, created_at, steps: list) -> dict:
    return {
        "session_id": session_id,
        "message_id": message_id,
        "intent": intent,
        "confidence": confidence,
        "sentiment": sentiment,
        "total_elapsed_ms": elapsed_ms,
        "created_at": created_at,
        "steps": steps,
    }


_DEMO_KNOWLEDGE_STEPS = [
    {"step": "router", "label": "理解客户问题", "intent": "knowledge_consult", "confidence": 0.93, "elapsed_ms": 36},
    {"step": "retrieval", "label": "查找退货政策", "query": "七天无理由退货流程", "result_count": 5, "top_scores": [0.91, 0.84, 0.78], "elapsed_ms": 421},
    {"step": "generation", "label": "根据资料生成回答", "sources_count": 1, "answer_preview": "支持七天无理由退货，并说明完整流程。", "elapsed_ms": 823},
]
_DEMO_ORDER_STEPS = [
    {"step": "router", "label": "识别订单查询", "intent": "order_query", "confidence": 0.98, "elapsed_ms": 31},
    {"step": "tool", "label": "核验身份并查询订单", "agent_type": "order_agent", "observation": "双因子校验通过；订单已发货，物流单号 SF1234567890。", "elapsed_ms": 219},
    {"step": "generation", "label": "整理业务结果", "sources_count": 0, "answer_preview": "订单金额 899 元，当前已发货。", "elapsed_ms": 36},
]
_DEMO_HANDOFF_STEPS = [
    {"step": "router", "label": "识别赔偿诉求", "intent": "sensitive_action", "confidence": 0.97, "elapsed_ms": 28},
    {"step": "safety", "label": "执行资金风险规则", "observation": "额外赔偿属于资金操作，必须由人工核验与审批。", "elapsed_ms": 42},
    {"step": "handoff", "label": "转入人工优先队列", "observation": "已保留完整对话与订单查询结果，坐席无需重新询问。", "elapsed_ms": 124},
]


def _build_demo_traces(db: DbSession, tenant_id: int, message_ids: dict[str, list[int]], now: datetime) -> list[dict]:
    from app.models.session import Session

    waiting = db.query(Session).filter_by(
        tenant_id=tenant_id, end_user_id="eu-demo-waiting"
    ).first()
    if waiting is None:
        return []
    ids = message_ids.get("eu-demo-waiting", [])
    assistant_ids = [ids[i] for i in (1, 3, 5) if len(ids) > i]
    message_id = assistant_ids[0] if assistant_ids else 0
    order_id = assistant_ids[1] if len(assistant_ids) > 1 else 0
    handoff_id = assistant_ids[2] if len(assistant_ids) > 2 else 0
    return [
        _demo_trace(waiting.id, message_id, "knowledge_consult", 0.93, "normal", 1280, now - timedelta(minutes=16), _DEMO_KNOWLEDGE_STEPS),
        _demo_trace(waiting.id, order_id, "order_query", 0.98, "normal", 286, now - timedelta(minutes=12), _DEMO_ORDER_STEPS),
        _demo_trace(waiting.id, handoff_id, "sensitive_action", 0.97, "unhappy", 194, now - timedelta(minutes=6), _DEMO_HANDOFF_STEPS),
    ]


def _seed_deep_agent_report(tenant_id: int, now: datetime) -> int:
    from app.core.deep_agent_store import save_task

    save_task({
        "task_id": DEMO_DEEP_TASK_ID,
        "goal": "分析本周工单积压与退款风险",
        "status": "completed",
        "tenant_id": tenant_id,
        "created_at": now - timedelta(hours=2),
        "completed_at": now - timedelta(hours=1, minutes=58),
        "total_elapsed_ms": 8420,
        "token_consumed": 6840,
        "token_budget": settings.deep_agent_token_budget,
        "termination_reason": "",
        "error": "",
        "steps": [
            {"step": 1, "label": "统计工单与退款", "elapsed_ms": 1260, "thought": "先建立本周业务基线", "action": "query_stats", "action_input": "本周工单和退款", "observation": "高优先级待处理工单 1 条，大额退款待终审 1 条"},
            {"step": 2, "label": "核对售后 SOP", "elapsed_ms": 2180, "thought": "判断积压是否存在流程缺口", "action": "search_sop", "action_input": "破损与退款流程", "observation": "破损商品应先建高优先级工单，再按证据进入退款审批"},
            {"step": 3, "label": "形成行动建议", "elapsed_ms": 4980, "thought": "把发现转化为可执行建议", "action": "finish", "action_input": "生成报告", "observation": "报告已生成"},
        ],
        "report": "# 本周客服运营简报\n\n## 关键发现\n\n- 自动解决率保持在 **78%–86%**，知识咨询表现稳定。\n- 当前有 **1 条高优先级破损工单** 与 **1 笔大额退款**需要处理。\n- 订单查询工具成功率接近 100%，身份核验链路正常。\n\n## 建议动作\n\n1. 今日内完成大额退款终审。\n2. 将运输破损证据收集话术加入坐席快捷回复。\n3. 补充特殊赔偿边界知识，继续保留人工审批。",
    })
    return 1


def restore_demo_story(
    db: DbSession,
    tenant_id: int,
    *,
    include_external: bool | None = None,
) -> dict:
    """恢复演示故事基线，返回真实写入计数。"""
    from app.models.tenant import Tenant

    if settings.app_env == "production":
        raise ValueError("生产环境禁止恢复演示故事")
    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if tenant is None or not tenant.is_demo or tenant.status != "active":
        raise ValueError("仅可恢复当前 active 演示租户")

    # 演示通道使用由应用密钥派生的稳定 Key，只保存 SHA-256；仅 demo 状态端点可在
    # 管理员已登录时重新派生返回，生产环境永远不可用。
    widget_key = demo_widget_api_key(tenant_id)
    tenant.widget_api_key_hash = hashlib.sha256(widget_key.encode("utf-8")).hexdigest()
    tenant.widget_allowed_domains = "localhost,127.0.0.1"
    tenant.widget_theme_color = "#14b8a6"
    tenant.widget_welcome_text = "你好，我是智枢客服。可以帮你查政策、订单，也会在需要时转接人工。"
    tenant.wizard_step = "done"
    db.commit()

    now = _utcnow()
    messages, message_ids = _seed_messages(db, tenant_id, now)
    if include_external is None:
        include_external = settings.app_env != "test"
    event_count = trace_count = deep_count = 0
    if include_external:
        from app.services.analytics_service import replace_demo_events
        from app.services.trace_service import replace_demo_traces

        event_count = replace_demo_events(tenant_id, build_demo_events(now))
        trace_count = replace_demo_traces(
            tenant_id, _build_demo_traces(db, tenant_id, message_ids, now)
        )
        deep_count = _seed_deep_agent_report(tenant_id, now)
    return {
        "tenant_id": tenant_id,
        "messages": messages,
        "analytics_events": event_count,
        "traces": trace_count,
        "deep_agent_tasks": deep_count,
    }
