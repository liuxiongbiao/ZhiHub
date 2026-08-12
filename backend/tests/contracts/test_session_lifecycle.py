"""
backend/tests/contracts/test_session_lifecycle.py

职责: 测试 007 会话生命周期与转人工增强的契约（轮次转人工/空闲收尾/留言建单/CSAT/限流）

测试内容:
  - TestRoundBasedHandoff: 轮次计数（连续3轮可解决类意图相同 / 累计15轮转人工；
    知识咨询不计 consecutive；意图变化重置 streak；reset 保留 total）
  - TestIdleCleanup: 空闲超时收尾（agent_handling/waiting_human 30min->resolved；
    resolved 60min->closed；活跃会话跳过）
  - TestAgentPresence: 在线坐席 presence（Redis SET 标记 + 租户隔离）
  - TestLeaveMessage: 留言建单（无坐席->建单 category=message；有坐席 409；非 waiting_human 403）
  - TestCSAT: CSAT 满意度（仅 closed 可提交；不可重复）
  - TestMessageRateLimit: 对话限流（10次/分钟，第11条 429；429 不落库不计轮次）
"""

import pytest


# ═══════════════════════════════════════════════════════════
# T002: 轮次触发转人工（User Story 1）
# SC-001: 连续3轮可解决类意图未解决 / 累计15轮 100% 转人工；知识咨询不计入 consecutive
# ═══════════════════════════════════════════════════════════
class TestRoundBasedHandoff:
    """session_service 轮次计数契约。"""

    def test_consecutive_3_resolvable_triggers_handoff(self):
        """连续3轮可解决类意图相同 -> should_handoff_by_rounds True。"""
        from app.services.session_service import incr_rounds, should_handoff_by_rounds

        sid = 9001
        for _ in range(3):
            rounds = incr_rounds(sid, "ticket_query")
        assert rounds["consecutive"] == 3
        assert should_handoff_by_rounds(rounds) is True

    def test_knowledge_consult_no_consecutive(self):
        """知识咨询不计入 consecutive，连发3轮不触发（仅 total 增长）。"""
        from app.services.session_service import incr_rounds, should_handoff_by_rounds

        sid = 9002
        for _ in range(3):
            rounds = incr_rounds(sid, "knowledge_consult")
        assert rounds["consecutive"] == 0
        assert should_handoff_by_rounds(rounds) is False
        assert rounds["total"] == 3

    def test_intent_change_starts_new_streak(self):
        """可解决类意图变化 -> 旧 streak 结束，新意图 streak 从 1 起。"""
        from app.services.session_service import incr_rounds

        sid = 9003
        incr_rounds(sid, "ticket_query")
        incr_rounds(sid, "ticket_query")
        rounds = incr_rounds(sid, "order_query")  # 意图变了
        assert rounds["consecutive"] == 1  # order_query 新 streak
        assert rounds["total"] == 3

    def test_total_15_triggers_handoff(self):
        """累计15轮（任意意图）-> should_handoff True。"""
        from app.services.session_service import incr_rounds, should_handoff_by_rounds

        sid = 9004
        rounds = {"total": 0, "consecutive": 0, "last_intent": None}
        for i in range(15):
            # 交替意图，consecutive 不会达 3，但 total 达 15
            intent = "ticket_query" if i % 2 == 0 else "order_query"
            rounds = incr_rounds(sid, intent)
        assert rounds["total"] == 15
        assert should_handoff_by_rounds(rounds) is True

    def test_reset_consecutive_keeps_total(self):
        """坐席退回 AI -> reset consecutive=0，total 保留。"""
        from app.services.session_service import incr_rounds, reset_consecutive, get_rounds

        sid = 9005
        for _ in range(3):
            incr_rounds(sid, "ticket_query")
        reset_consecutive(sid)
        rounds = get_rounds(sid)
        assert rounds["consecutive"] == 0
        assert rounds["total"] == 3  # total 保留

    def test_below_threshold_no_handoff(self):
        """2轮可解决意图相同 -> 不触发。"""
        from app.services.session_service import incr_rounds, should_handoff_by_rounds

        sid = 9006
        for _ in range(2):
            rounds = incr_rounds(sid, "ticket_query")
        assert should_handoff_by_rounds(rounds) is False


# ═══════════════════════════════════════════════════════════
# T005: 空闲超时收尾（User Story 2）
# SC-002: 30min 无活动 -> RESOLVED；再 30min -> CLOSED；活跃会话跳过
# ═══════════════════════════════════════════════════════════
import datetime
from sqlalchemy import text


def _set_updated_at(db, session_id, minutes_ago):
    """用 raw SQL 把 updated_at 设为 N 分钟前（绕过 ORM onupdate）。"""
    old = datetime.datetime.utcnow() - datetime.timedelta(minutes=minutes_ago)
    db.execute(text("UPDATE sessions SET updated_at=:old WHERE id=:id"), {"old": old, "id": session_id})
    db.commit()


class TestIdleCleanup:
    """cleanup_idle_sessions 定时收尾契约。"""

    def test_agent_handling_30min_to_resolved(self, db):
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "agent_handling")
        db.commit()
        _set_updated_at(db, s.id, 31)

        result = cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "resolved"
        assert result["resolved"] >= 1

    def test_waiting_human_30min_to_resolved(self, db):
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()
        _set_updated_at(db, s.id, 31)

        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "resolved"

    def test_resolved_60min_to_closed(self, db):
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "resolved")
        db.commit()
        _set_updated_at(db, s.id, 61)

        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "closed"

    def test_active_session_not_collected(self, db):
        """活跃会话（updated_at 新鲜）不被误收尾。"""
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "agent_handling")
        db.commit()
        _set_updated_at(db, s.id, 5)  # 5 分钟前，新鲜

        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "agent_handling"  # 未被收尾

    def test_closed_not_touched(self, db):
        """已 closed 的会话不再被处理。"""
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "closed")
        db.commit()
        _set_updated_at(db, s.id, 120)

        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "closed"


# ═══════════════════════════════════════════════════════════
# T008: 留言建单 + CSAT（User Story 3）
# SC-003: 无坐席留言 100% 建单 category=message；有坐席 409；
#         CSAT 仅 closed 可提交且不可重复
# ═══════════════════════════════════════════════════════════
class TestAgentPresence:
    """在线坐席 presence 契约。"""

    def test_no_agent_online(self):
        from app.services.session_service import has_online_agent
        assert has_online_agent(1) is False

    def test_agent_online_after_mark(self):
        from app.services.session_service import mark_agent_online, mark_agent_offline, has_online_agent
        mark_agent_online(1, 100)
        assert has_online_agent(1) is True
        mark_agent_offline(1, 100)
        assert has_online_agent(1) is False

    def test_different_tenant_isolated(self):
        from app.services.session_service import mark_agent_online, has_online_agent
        mark_agent_online(1, 100)
        assert has_online_agent(2) is False  # 租户2 无坐席


class TestLeaveMessage:
    """留言建单端点契约。"""

    def test_leave_message_no_agent_creates_ticket(self, client, db, make_token):
        """无在线坐席 + waiting_human -> 建单 category=message。"""
        from app.crud import session as session_crud
        from app.models.ticket import Ticket
        from sqlalchemy import select

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/{s.id}/leave-message",
            json={"content": "我下班后再联系"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        ticket_no = r.json()["ticket_no"]
        # DB 验证 category=message
        ticket = db.execute(select(Ticket).where(Ticket.ticket_no == ticket_no)).scalar_one()
        assert ticket.category == "message"

    def test_leave_message_with_agent_rejected(self, client, db, make_token):
        """有在线坐席 -> 409。"""
        from app.crud import session as session_crud
        from app.services.session_service import mark_agent_online

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()
        mark_agent_online(1, 100)  # 模拟坐席在线
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/{s.id}/leave-message",
            json={"content": "留言"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

    def test_leave_message_wrong_status_rejected(self, client, db, make_token):
        """非 waiting_human 状态 -> 403。"""
        from app.crud import session as session_crud

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "agent_handling")  # 非 waiting_human
        db.commit()
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/{s.id}/leave-message",
            json={"content": "留言"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestCSAT:
    """CSAT 满意度评价端点契约。"""

    def test_csat_closed_session_ok(self, client, db, make_token):
        from app.crud import session as session_crud

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "closed")
        db.commit()
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/{s.id}/csat",
            json={"score": 5, "comment": "服务好"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["score"] == 5

    def test_csat_duplicate_rejected(self, client, db, make_token):
        from app.crud import session as session_crud

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "closed")
        db.commit()
        token = make_token(tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}

        r1 = client.post(f"/api/v1/session/{s.id}/csat", json={"score": 4}, headers=headers)
        assert r1.status_code == 201
        r2 = client.post(f"/api/v1/session/{s.id}/csat", json={"score": 3}, headers=headers)
        assert r2.status_code == 409

    def test_csat_non_closed_rejected(self, client, db, make_token):
        from app.crud import session as session_crud

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "resolved")  # 非 closed
        db.commit()
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/{s.id}/csat",
            json={"score": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════
# T014: 终端用户对话限流（User Story 4）
# SC-004: 单用户 10次/分钟，第11条 429；不同用户独立；429 不落库不计轮次
# ═══════════════════════════════════════════════════════════
class TestMessageRateLimit:
    """对话消息限流契约。"""

    def test_10_allowed_11th_denied(self):
        """同一用户 10 条通过，第 11 条被拒。"""
        from app.services.rate_limit_service import check_message_rate_limit

        uid = 9101
        for _ in range(10):
            r = check_message_rate_limit(uid)
            assert r["allowed"] is True
        r = check_message_rate_limit(uid)
        assert r["allowed"] is False

    def test_different_users_independent(self):
        """用户 A 达上限不影响用户 B。"""
        from app.services.rate_limit_service import check_message_rate_limit

        for _ in range(10):
            check_message_rate_limit(9102)
        assert check_message_rate_limit(9102)["allowed"] is False
        assert check_message_rate_limit(9103)["allowed"] is True  # 用户 B 正常

    def test_message_endpoint_429_not_persisted(self, client, db, make_token):
        """预填 10 次后 POST /session/message -> 429，且不落库 messages。"""
        from app.crud import session as session_crud
        from app.services.rate_limit_service import check_message_rate_limit
        from app.models.message import Message
        from sqlalchemy import select, func

        s = session_crud.create_session(db, tenant_id=1)
        db.commit()
        token = make_token(user_id=9104, tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}

        # 预填 user_id=9104 的限流计数到上限（端点用同一 user_id）
        for _ in range(10):
            check_message_rate_limit(9104)

        r = client.post(
            "/api/v1/session/message",
            json={"session_id": s.id, "content": "第11条"},
            headers=headers,
        )
        assert r.status_code == 429
        # 429 不落库 messages
        msg_count = db.execute(
            select(func.count()).select_from(Message).where(Message.session_id == s.id)
        ).scalar() or 0
        assert msg_count == 0

    def test_degraded_tenant_returns_200_not_500(self, client, db, make_token):
        """H2 回归: 降级租户发消息应返回 200 + 降级提示，而非 response_model 校验失败 500。

        降级返回 message_id=None（不落库），原 SendMessageResponse.message_id:int 非空
        -> FastAPI ResponseValidationError -> 500。修复后 message_id 可空 + metadata.degraded 透传。
        """
        from app.crud import session as session_crud
        from app.core.redis import get_redis

        s = session_crud.create_session(db, tenant_id=1)
        db.commit()
        get_redis().set("billing:degraded:1", "1", ex=86400)  # 标记租户降级

        token = make_token(user_id=9201, tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}
        r = client.post(
            "/api/v1/session/message",
            json={"session_id": s.id, "content": "你好"},
            headers=headers,
        )
        assert r.status_code == 200, f"降级路径不应 500: {r.status_code} {r.text}"
        body = r.json()
        assert body["message_id"] is None
        assert body["metadata"]["degraded"] is True
        assert "降级" in body["content"]
