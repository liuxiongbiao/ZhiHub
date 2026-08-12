"""
backend/tests/integration/test_session_lifecycle_e2e.py

职责: 007 会话生命周期与转人工增强的端到端 4 场景集成测试

测试内容:
  - TestE2ERoundsHandoff: 连续3轮可解决类意图 -> 转人工；知识咨询豁免
  - TestE2EIdleCleanup: agent_handling 31min->resolved->61min->closed；活跃会话保留
  - TestE2ELeaveMessageAndCSAT: 转人工无坐席留言建单 -> 关闭 -> CSAT -> 重复 409
  - TestE2EMessageRateLimit: 预填10次后第11条 429，且不落库 messages

设计: TestClient 走真实中间件链路 + 内存 DB + fakeredis；轮次/空闲走服务层直接调用（避免 LLM 依赖）。
"""

import datetime

from sqlalchemy import text


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _set_updated_at(db, session_id, minutes_ago):
    old = datetime.datetime.utcnow() - datetime.timedelta(minutes=minutes_ago)
    db.execute(text("UPDATE sessions SET updated_at=:old WHERE id=:id"), {"old": old, "id": session_id})
    db.commit()


# ═══════════════════════════════════════════════════════════
# 场景 1: 轮次触发转人工（quickstart 1.x）
# ═══════════════════════════════════════════════════════════
class TestE2ERoundsHandoff:
    def test_consecutive_3_triggers(self):
        """连续3轮可解决类意图相同 -> 达转人工阈值。"""
        from app.services.session_service import incr_rounds, should_handoff_by_rounds

        sid = 20001
        for _ in range(3):
            rounds = incr_rounds(sid, "ticket_query")
        assert should_handoff_by_rounds(rounds) is True

    def test_knowledge_consult_exempt(self):
        """知识咨询连发3轮不触发（仅 total 增长）。"""
        from app.services.session_service import incr_rounds, should_handoff_by_rounds

        sid = 20002
        for _ in range(3):
            rounds = incr_rounds(sid, "knowledge_consult")
        assert should_handoff_by_rounds(rounds) is False


# ═══════════════════════════════════════════════════════════
# 场景 2: 空闲超时收尾（quickstart 2.x）
# ═══════════════════════════════════════════════════════════
class TestE2EIdleCleanup:
    def test_full_lifecycle_to_closed(self, db):
        """agent_handling 31min -> resolved -> 再 61min -> closed。"""
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "agent_handling")
        db.commit()

        # 31min -> resolved
        _set_updated_at(db, s.id, 31)
        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "resolved"

        # 61min -> closed
        _set_updated_at(db, s.id, 61)
        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "closed"

    def test_active_session_preserved(self, db):
        from app.crud import session as session_crud
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "agent_handling")
        db.commit()
        _set_updated_at(db, s.id, 5)
        cleanup_idle_sessions(db)
        db.refresh(s)
        assert s.status == "agent_handling"


# ═══════════════════════════════════════════════════════════
# 场景 3: 留言建单 + CSAT（quickstart 3.x）
# ═══════════════════════════════════════════════════════════
class TestE2ELeaveMessageAndCSAT:
    def test_leave_then_csat_flow(self, client, db, make_token):
        """转人工无坐席留言建单 -> 关闭会话 -> CSAT 评价。"""
        from app.crud import session as session_crud
        from app.models.ticket import Ticket
        from sqlalchemy import select

        # 会话转人工态
        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()
        token = make_token(tenant_id=1, role="agent")

        # 留言建单（无在线坐席）
        r = client.post(
            f"/api/v1/session/{s.id}/leave-message",
            json={"content": "下班后再联系"},
            headers=_auth(token),
        )
        assert r.status_code == 201
        ticket_no = r.json()["ticket_no"]
        ticket = db.execute(select(Ticket).where(Ticket.ticket_no == ticket_no)).scalar_one()
        assert ticket.category == "message"

        # 关闭会话后 CSAT
        session_crud.update_status(db, s, "closed")
        db.commit()
        r = client.post(
            f"/api/v1/session/{s.id}/csat",
            json={"score": 5, "comment": "满意"},
            headers=_auth(token),
        )
        assert r.status_code == 201
        # 重复评价被拒
        r2 = client.post(
            f"/api/v1/session/{s.id}/csat",
            json={"score": 4},
            headers=_auth(token),
        )
        assert r2.status_code == 409


# ═══════════════════════════════════════════════════════════
# 场景 4: 终端用户限流（quickstart 4.x）
# ═══════════════════════════════════════════════════════════
class TestE2EMessageRateLimit:
    def test_11th_message_429(self, client, db, make_token):
        """预填10次后第11条 429，且不落库。"""
        from app.crud import session as session_crud
        from app.services.rate_limit_service import check_message_rate_limit
        from app.models.message import Message
        from sqlalchemy import select, func

        s = session_crud.create_session(db, tenant_id=1)
        db.commit()
        token = make_token(user_id=20003, tenant_id=1, role="agent")

        for _ in range(10):
            check_message_rate_limit(20003)

        r = client.post(
            "/api/v1/session/message",
            json={"session_id": s.id, "content": "第11条"},
            headers=_auth(token),
        )
        assert r.status_code == 429
        msg_count = db.execute(
            select(func.count()).select_from(Message).where(Message.session_id == s.id)
        ).scalar() or 0
        assert msg_count == 0
