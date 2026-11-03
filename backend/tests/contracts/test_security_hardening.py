"""本轮安全加固的契约：CSRF 头值、Redis 故障拒绝、熔断锁、生产口令与假统计。"""

import json

import pytest
from pydantic import ValidationError


def test_csrf_rejects_token_that_does_not_match_bearer(client, make_token):
    token = make_token()
    resp = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": "not-the-token"},
    )
    assert resp.status_code == 403


def test_blocklist_redis_outage_returns_401(client, make_token, monkeypatch):
    from app.middlewares import auth_middleware

    def _boom(jti):
        raise ConnectionError("redis down")

    monkeypatch.setattr(auth_middleware, "is_blocklisted", _boom)
    token = make_token()
    resp = client.get("/api/v1/tenant/home", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_login_lock_store_outage_returns_503(client, monkeypatch):
    from app.core import redis as redis_module

    def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_module, "get_lock_ttl", _boom)
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138000", "password": "abc12345"},
    )
    assert resp.status_code == 503


def test_circuit_lock_failure_does_not_mutate(monkeypatch):
    from app.core import circuit_breaker as cb

    monkeypatch.setattr(cb, "MUTATION_LOCK_RETRIES", 1)
    monkeypatch.setattr(cb, "MUTATION_LOCK_RETRY_DELAY", 0)
    saved: list[dict] = []
    state = {
        "state": "closed", "fail_count": 0, "recover_count": 0,
        "last_failed_at": None, "opened_at": None,
    }

    class _Redis:
        def get(self, key):
            if str(key).endswith(":lock") or str(key).endswith(":probe"):
                return None
            return json.dumps(state)

        def set(self, *args, **kwargs):
            return False

        def delete(self, *args, **kwargs):
            return 0

    monkeypatch.setattr(cb, "get_redis", lambda: _Redis())
    monkeypatch.setattr(cb, "_save", lambda name, new_state: saved.append(new_state))
    cb.record_failure("tool-lock")
    assert saved == []


def test_production_rejects_default_datastore_passwords():
    from app.config import Settings

    with pytest.raises(ValidationError, match="MYSQL_PASSWORD"):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="production-secret",
            email_verify_required=False,
            email_enabled=False,
            mysql_password="zhihub",
            redis_password="zhihub-dev",
            rabbitmq_url="amqp://zhihub:zhihub-dev@rabbitmq:5672/",
            mongo_uri="mongodb://mongo:27017/zhihub",
            elasticsearch_url="http://elasticsearch:9200",
        )


def test_development_keeps_local_datastore_defaults():
    from app.config import Settings

    assert Settings.model_fields["mysql_password"].default == "zhihub"
    assert Settings.model_fields["router_confidence_threshold"].default == 0.4
    Settings(
        _env_file=None,
        app_env="development",
        app_secret_key="change-me",
        mysql_password="zhihub",
        redis_password="",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        mongo_uri="mongodb://localhost:27017/zhihub",
        elasticsearch_url="http://localhost:9200",
        email_verify_required=False,
    )


def test_production_init_db_refuses_create_all(monkeypatch):
    from app.core import database

    monkeypatch.setattr(database.settings, "app_env", "production")

    class _Inspector:
        def get_table_names(self):
            return []

    monkeypatch.setattr(database, "inspect", lambda engine: _Inspector())
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        database.init_db()


def test_query_stats_does_not_invent_counts(monkeypatch):
    from app.agents.deep_agent import planner

    def _query(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.core.database.SessionLocal", _query)
    stats = planner._query_stats(1)
    assert stats == {"total_tickets": None, "open_tickets": None, "source": "unavailable"}
    assert "25" not in json.dumps(stats)

    def _llm_down(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.core.llm_client.llm_complete", _llm_down)
    report = planner._generate_report("工单分析", stats, [])
    assert "统计不可用" in report
    assert "25" not in report
