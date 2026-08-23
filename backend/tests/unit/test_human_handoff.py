"""
backend/tests/unit/test_human_handoff.py

职责: 测试转人工触发条件 + 坐席认领分布式锁 + 限流/计费配额常量

测试内容:
  - TestHumanHandoff: 愤怒/敏感操作/低置信度触发转人工 + 多原因叠加
  - TestClaimSessionLogic: SETNX 原子锁概念 + SessionAlreadyClaimed 异常
  - TestRateLimit: 租户/用户限流常量（TENANT_RATE=100, USER_RATE=20）
  - TestBillingQuota: 配额 key 格式 + 预警阈值（80%/100%/TRIAL_QUOTA=100）
"""

import pytest


class TestHumanHandoff:
    def test_angry_triggers_reason(self):
        from app.agents.nodes.human_handoff import human_handoff_node
        state = {"sentiment": "angry", "intent": "knowledge_consult", "confidence": 0.9, "session_id": 1}
        result = human_handoff_node(state)
        assert result["should_interrupt"] is True
        assert "情绪愤怒" in result["final_response"]

    def test_sensitive_action_triggers(self):
        from app.agents.nodes.human_handoff import human_handoff_node
        state = {"sentiment": "normal", "intent": "sensitive_action", "confidence": 0.9, "session_id": 1}
        result = human_handoff_node(state)
        assert "敏感操作" in result["final_response"]

    def test_low_confidence_triggers(self):
        from app.agents.nodes.human_handoff import human_handoff_node
        state = {"sentiment": "normal", "intent": "knowledge_consult", "confidence": 0.2, "session_id": 1}
        result = human_handoff_node(state)
        assert "置信度不足" in result["final_response"]

    def test_multiple_reasons(self):
        from app.agents.nodes.human_handoff import human_handoff_node
        state = {"sentiment": "angry", "intent": "sensitive_action", "confidence": 0.3, "session_id": 1}
        result = human_handoff_node(state)
        assert result["should_interrupt"] is True


class TestClaimSessionLogic:
    def test_setnx_atomicity_concept(self, fake_redis):
        """验证 Redis SETNX 原子锁：第一次设置成功，第二次同一 key 返回 False."""
        from app.services.human_service import CLAIM_LOCK_PREFIX, CLAIM_LOCK_TTL

        lock_key = f"{CLAIM_LOCK_PREFIX}999"
        # 第一次 SETNX: key 不存在 → True
        first = fake_redis.set(lock_key, "agent-1", ex=CLAIM_LOCK_TTL, nx=True)
        assert first is True
        # 第二次 SETNX: key 已存在 → None (False)
        second = fake_redis.set(lock_key, "agent-2", ex=CLAIM_LOCK_TTL, nx=True)
        assert second is None

    def test_session_already_claimed_error(self, db):
        """同一会话被两次认领 -> 第二次抛 SessionAlreadyClaimed."""
        from app.crud import session as session_crud
        from app.services.human_service import claim_session, SessionAlreadyClaimed

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()

        # 第一次认领 → 成功
        result = claim_session(db, session_id=s.id, agent_id=101, tenant_id=1)
        assert result["status"] == "human_handling"

        # 第二次认领（不同坐席）→ Redis 锁仍存在 → SessionAlreadyClaimed
        with pytest.raises(SessionAlreadyClaimed):
            claim_session(db, session_id=s.id, agent_id=202, tenant_id=1)


class TestRateLimit:
    def test_within_limit_allowed(self):
        """额限内允许."""
        # 概念验证: check_rate_limit 需要 Redis
        from app.services.rate_limit_service import TENANT_RATE, USER_RATE
        assert TENANT_RATE == 100
        assert USER_RATE == 20

    def test_over_limit_denied(self):
        """超限后 check_rate_limit 返回 allowed=False（调用真实限流 service + fakeredis）."""
        from app.services.rate_limit_service import check_rate_limit, USER_RATE

        # 用尽用户配额（USER_RATE=20 次全部 allowed）
        for i in range(USER_RATE):
            r = check_rate_limit(tenant_id=1, user_id=1)
            assert r["allowed"] is True
        # 第 USER_RATE+1 次 → 用户维度超限 → allowed=False
        result = check_rate_limit(tenant_id=1, user_id=1)
        assert result["allowed"] is False
        assert result["user_count"] >= USER_RATE


class TestBillingQuota:
    def test_quota_key_format(self):
        from app.services.billing_service import _quota_key
        key = _quota_key(1)
        assert key.startswith("billing:quota:1:")
        assert len(key) > 15

    def test_warning_thresholds(self):
        from app.services.billing_service import QUOTA_WARNING_80, QUOTA_WARNING_100, TRIAL_QUOTA
        assert QUOTA_WARNING_80 == 0.8
        assert QUOTA_WARNING_100 == 1.0
        assert TRIAL_QUOTA == 100
