"""
backend/tests/contracts/test_batch4_reliability.py

职责: 批次 4 契约落点、权限、异步文件处理、多进程广播与启动配置回归测试。
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import fakeredis
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.ws_manager import ConnectionManager
from app.crud import knowledge as knowledge_crud
from app.crud import user as user_crud
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.services import tenant_service
from app.tasks import knowledge_tasks


def test_production_email_activation_requires_external_https_url() -> None:
    with pytest.raises(ValidationError, match="EMAIL_VERIFY_LINK_BASE"):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="production-secret",
            email_verify_required=True,
            smtp_host="smtp.example.com",
            smtp_user="user",
            smtp_password="secret",
            smtp_from="noreply@example.com",
            email_verify_link_base="http://localhost:5173",
        )


def test_enabled_features_report_all_missing_configuration() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="production-secret",
            email_enabled=False,
            payment_enabled=True,
            sms_enabled=True,
            wechat_enabled=True,
            llm_enabled=True,
            embedding_enabled=True,
            alipay_app_id="",
            alipay_app_private_key="",
            alipay_public_key="",
            alipay_notify_url="",
            aliyun_sms_access_key_id="",
            aliyun_sms_access_key_secret="",
            aliyun_sms_sign_name="",
            aliyun_sms_template_code="",
            wechat_app_id="",
            wechat_app_secret="",
            wechat_redirect_uri="",
            deepseek_api_key="",
            embedding_api_key="",
        )

    message = str(exc_info.value)
    for key in (
        "ALIPAY_APP_ID",
        "ALIYUN_SMS_ACCESS_KEY_ID",
        "WECHAT_APP_ID",
        "DEEPSEEK_API_KEY",
        "EMBEDDING_API_KEY",
    ):
        assert key in message


def _tenant(db) -> Tenant:
    tenant = Tenant(name="Batch 4", seat_limit=2)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_invite_response_contains_frontend_url(db, monkeypatch) -> None:
    tenant = _tenant(db)
    monkeypatch.setattr(tenant_service.settings, "invite_link_base", "https://app.example.com/accept-invite")
    tenant.seat_limit = 2
    db.commit()

    result = tenant_service.invite_member(db, tenant.id, "13900000011")

    assert result["invite_token"]
    assert result["invite_url"] == (
        f"https://app.example.com/accept-invite?token={result['invite_token']}"
    )


def test_logged_in_user_accepts_invite_and_receives_target_tenant_token(
    db, client, make_token,
) -> None:
    tenant = _tenant(db)
    user = user_crud.create(
        db,
        phone="13900000012",
        password_hash=hash_password("abc12345"),
        role_default="agent",
    )
    db.commit()
    invite = tenant_service.invite_member(db, tenant.id, user.phone)

    response = client.post(
        "/api/v1/tenant/invite/accept",
        headers={"Authorization": f"Bearer {make_token(user.id, 999, 'agent')}"},
        json={"token": invite["invite_token"]},
    )

    assert response.status_code == 200
    assert response.json()["tenant_id"] == tenant.id
    assert response.json()["access_token"]


@pytest.mark.parametrize("path,method", [
    ("/api/v1/knowledge/list", "get"),
    ("/api/v1/knowledge/test", "post"),
])
def test_agent_cannot_access_knowledge_management_endpoints(client, make_token, path, method) -> None:
    headers = {"Authorization": f"Bearer {make_token(role='agent')}"}
    if method == "post":
        response = client.post(path, headers=headers, json={"query": "refund"})
    else:
        response = client.get(path, headers=headers)
    assert response.status_code == 403


def test_missing_uploaded_file_marks_document_failed(db, monkeypatch) -> None:
    tenant = _tenant(db)
    doc = knowledge_crud.create(
        db,
        tenant_id=tenant.id,
        filename="missing.pdf",
        file_type="pdf",
        file_size=10,
        version=1,
        expires_at=None,
    )
    db.commit()
    doc_id = doc.id
    tenant_id = tenant.id
    missing_dir = Path(__file__).parent / "_missing_batch4_uploads"
    monkeypatch.setattr(knowledge_tasks.settings, "upload_storage_dir", str(missing_dir))
    monkeypatch.setattr(knowledge_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(knowledge_tasks, "init_milvus_collection", lambda: None)
    monkeypatch.setattr(knowledge_tasks, "init_es_index", lambda: None)

    knowledge_tasks.process_document(doc_id, tenant_id)
    doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)

    assert doc is not None
    assert doc.status == "failed"
    assert doc.chunk_count == 0
    assert "文件" in (doc.error_message or "")


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_redis_pubsub_delivers_between_two_worker_managers() -> None:
    server = fakeredis.FakeServer()
    redis_a = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    redis_b = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    manager_a = ConnectionManager(channel="test:ws:batch4")
    manager_b = ConnectionManager(channel="test:ws:batch4")
    socket = _Socket()

    await manager_a.start_pubsub(redis_a)
    await manager_b.start_pubsub(redis_b)
    await manager_a.connect(socket, tenant_id=7)
    try:
        await manager_b.broadcast_to_tenant(7, {"type": "new_session", "payload": {"session_id": 9}})
        for _ in range(20):
            if socket.messages:
                break
            await asyncio.sleep(0.01)
        assert socket.messages == [
            {"type": "new_session", "payload": {"session_id": 9}, "event": "new_session", "session_id": 9}
        ]
    finally:
        await manager_a.stop_pubsub()
        await manager_b.stop_pubsub()
        await redis_a.aclose()
        await redis_b.aclose()


def test_fastapi_lifespan_does_not_start_business_threads() -> None:
    from app import main

    source = inspect.getsource(main.lifespan)
    assert "threading.Thread" not in source
    assert "_run_knowledge_consumer" not in source
    assert "_run_idle_cleanup" not in source
    assert "_run_billing_scheduler" not in source


def test_celery_beat_owns_maintenance_schedule() -> None:
    from celery_worker import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule["periodic-maintenance"]["task"] == "app.tasks.cleanup_tasks.run_periodic_maintenance"
    assert schedule["periodic-maintenance"]["options"]["queue"] == "maintenance"
    assert celery_app.conf.task_routes["app.tasks.knowledge_tasks.process_document"]["queue"] == "knowledge"


def test_upload_storage_directory_is_not_system_temp() -> None:
    settings = Settings(_env_file=None)
    assert settings.upload_storage_dir
    assert Path(settings.upload_storage_dir).name == "uploads"
