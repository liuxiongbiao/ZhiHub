"""
backend/tests/contracts/test_alerts.py

职责: 测试降级模式告警横幅（#8, PRD 3.4/3.8）+ 预警站内红色横幅（#9, PRD 3.9）

测试内容:
  - TestDegradationBanner: 检索降级标记
    - vector_recall 失败 -> Redis 标记 degradation:{tenant_id}（TTL 1h）
    - bm25_recall 失败 -> Redis 标记 degradation:{tenant_id}（TTL 1h）
    - 成功时不标记
    - /home 返回 degradation_alert 字段（有标记=True / 无标记=False）
  - TestWarningBanner: 配额预警横幅
    - incr_usage 达 80% -> Redis 标记 warning_banner:{tenant}:{80}
    - incr_usage 达 100% -> Redis 标记 warning_banner:{tenant}:{100}
    - /home 返回 warning_banner 字段（100% 优先于 80%）

  - 降级标记用 Redis 而非 DB: 检索基础设施故障是临时状态，Redis TTL 自然过期恢复，
    无需人工清理。TTL 1h 平衡敏感度与抖动（太短用户看不到，太长故障恢复后仍显示）。
  - 预警横幅用独立键 warning_banner:{tenant}:{level}: 与计费去重键 billing:warning 分离，
    职责单一 -- billing:warning 管通知去重，warning_banner 管 UI 横幅显示。
  - /home 聚合两种横幅: 前端只需调一个接口拿全部告警状态，减少请求往返。
"""

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, phone="13800138000"):
    code = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()["code"]
    return client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "abc12345", "tenant_name": "示例科技", "code": code},
    ).json()


# ═══════════════════════════════════════════════════════════
# #8 降级模式告警横幅（PRD 3.4/3.8）
# ═══════════════════════════════════════════════════════════
class TestDegradationBanner:
    """检索降级标记: vector/bm25 召回失败时 Redis 标记 degradation:{tenant_id}。"""

    def test_vector_recall_failure_sets_degradation(self, monkeypatch, fake_redis):
        """vector_recall 失败 -> 设置 degradation:{tenant_id} 标记。"""
        from app.services import retrieval_service

        class FakeCollection:
            def search(self, **kwargs):
                raise RuntimeError("Milvus down")

        monkeypatch.setattr(retrieval_service, "get_milvus_collection", lambda: FakeCollection())
        monkeypatch.setattr(retrieval_service, "embed_texts", lambda texts: [[0.1, 0.2]])

        result = retrieval_service.vector_recall("test query", tenant_id=42)
        assert result == []
        assert fake_redis.exists("degradation:42") == 1

    def test_bm25_recall_failure_sets_degradation(self, monkeypatch, fake_redis):
        """bm25_recall 失败 -> 设置 degradation:{tenant_id} 标记。"""
        from app.services import retrieval_service

        class FakeES:
            def search(self, **kwargs):
                raise RuntimeError("ES down")

        monkeypatch.setattr(retrieval_service, "get_es_client", lambda: FakeES())

        result = retrieval_service.bm25_recall("test query", tenant_id=42)
        assert result == []
        assert fake_redis.exists("degradation:42") == 1

    def test_degradation_ttl_one_hour(self, monkeypatch, fake_redis):
        """降级标记 TTL = 3600 秒（1 小时）。"""
        from app.services import retrieval_service

        class FakeCollection:
            def search(self, **kwargs):
                raise RuntimeError("Milvus down")

        monkeypatch.setattr(retrieval_service, "get_milvus_collection", lambda: FakeCollection())
        monkeypatch.setattr(retrieval_service, "embed_texts", lambda texts: [[0.1, 0.2]])

        retrieval_service.vector_recall("test", tenant_id=99)
        ttl = fake_redis.ttl("degradation:99")
        assert 3500 <= ttl <= 3600  # 允许微小时间偏差

    def test_no_degradation_on_success(self, monkeypatch, fake_redis):
        """检索成功时不设置降级标记。"""
        from app.services import retrieval_service

        class FakeCollection:
            def search(self, **kwargs):
                return [[]]  # 空结果但无异常

        monkeypatch.setattr(retrieval_service, "get_milvus_collection", lambda: FakeCollection())
        monkeypatch.setattr(retrieval_service, "embed_texts", lambda texts: [[0.1, 0.2]])

        retrieval_service.vector_recall("test", tenant_id=77)
        assert fake_redis.exists("degradation:77") == 0

    def test_home_returns_degradation_alert_true(self, client, make_token, fake_redis):
        """/home 有降级标记时返回 degradation_alert=True。"""
        reg = _register(client)
        tenant_id = reg["tenant_id"]
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138000", "password": "abc12345"}
        ).json()
        fake_redis.set(f"degradation:{tenant_id}", "1", ex=3600)

        resp = client.get("/api/v1/tenant/home", headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["degradation_alert"] is True

    def test_home_returns_degradation_alert_false_by_default(self, client, make_token, fake_redis):
        """/home 无降级标记时返回 degradation_alert=False。"""
        reg = _register(client, phone="13800138001")
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138001", "password": "abc12345"}
        ).json()

        resp = client.get("/api/v1/tenant/home", headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["degradation_alert"] is False


# ═══════════════════════════════════════════════════════════
# #9 预警站内红色横幅（PRD 3.9）
# ═══════════════════════════════════════════════════════════
class TestWarningBanner:
    """配额预警横幅: incr_usage 触发预警时 Redis 标记 warning_banner:{tenant}:{level}。"""

    def test_incr_usage_80_sets_warning_banner(self, fake_redis):
        """incr_usage 达 80% 阈值 -> 设置 warning_banner:{tenant}:80。"""
        from app.services import billing_service

        # TRIAL_QUOTA=100, 80%=80。先设到 79，再 +1 触发 80%
        fake_redis.set(
            f"billing:quota:55:{self._current_month()}", "79"
        )
        result = billing_service.incr_usage(tenant_id=55)
        assert result["warning"] == "80%"
        assert fake_redis.exists("warning_banner:55:80") == 1

    def test_incr_usage_100_sets_warning_banner(self, fake_redis):
        """incr_usage 达 100% 阈值 -> 设置 warning_banner:{tenant}:100。"""
        from app.services import billing_service

        # 先设到 99，再 +1 触发 100%（80% 也会触发但 100% 更高）
        fake_redis.set(
            f"billing:quota:66:{self._current_month()}", "99"
        )
        # 预设 80% 已通知，避免 80% 抢先
        fake_redis.set(
            f"billing:warning:66:{self._current_month()}:80", "1"
        )
        result = billing_service.incr_usage(tenant_id=66)
        assert result["warning"] == "100%"
        assert fake_redis.exists("warning_banner:66:100") == 1

    def test_warning_banner_ttl_covers_month(self, fake_redis):
        """预警横幅 TTL 覆盖整月（>= 30 天）。"""
        from app.services import billing_service

        fake_redis.set(
            f"billing:quota:77:{self._current_month()}", "79"
        )
        billing_service.incr_usage(tenant_id=77)
        ttl = fake_redis.ttl("warning_banner:77:80")
        # 40 天 = 3456000 秒，至少 30 天 = 2592000
        assert ttl >= 2592000

    def test_home_returns_warning_banner_80(self, client, fake_redis):
        """/home 有 80% 预警标记时返回 warning_banner='80%'。"""
        reg = _register(client, phone="13800138002")
        tenant_id = reg["tenant_id"]
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138002", "password": "abc12345"}
        ).json()
        fake_redis.set(f"warning_banner:{tenant_id}:80", "1", ex=3456000)

        resp = client.get("/api/v1/tenant/home", headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        assert resp.json()["warning_banner"] == "80%"

    def test_home_returns_warning_banner_100(self, client, fake_redis):
        """/home 有 100% 预警标记时返回 warning_banner='100%'。"""
        reg = _register(client, phone="13800138003")
        tenant_id = reg["tenant_id"]
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138003", "password": "abc12345"}
        ).json()
        fake_redis.set(f"warning_banner:{tenant_id}:100", "1", ex=3456000)

        resp = client.get("/api/v1/tenant/home", headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        assert resp.json()["warning_banner"] == "100%"

    def test_home_warning_banner_100_overrides_80(self, client, fake_redis):
        """同时有 80% 和 100% 标记时，返回 100%（更高优先级）。"""
        reg = _register(client, phone="13800138004")
        tenant_id = reg["tenant_id"]
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138004", "password": "abc12345"}
        ).json()
        fake_redis.set(f"warning_banner:{tenant_id}:80", "1", ex=3456000)
        fake_redis.set(f"warning_banner:{tenant_id}:100", "1", ex=3456000)

        resp = client.get("/api/v1/tenant/home", headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        assert resp.json()["warning_banner"] == "100%"

    def test_home_no_warning_banner_by_default(self, client, fake_redis):
        """/home 无预警标记时返回 warning_banner=None。"""
        _register(client, phone="13800138005")
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138005", "password": "abc12345"}
        ).json()

        resp = client.get("/api/v1/tenant/home", headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        assert resp.json()["warning_banner"] is None

    @staticmethod
    def _current_month() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m")
