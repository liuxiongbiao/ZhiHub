"""
backend/tests/contracts/test_refresh_token_batch5.py

职责: JWT refresh token 轮换、泄露检测与撤销的批次 5 回归契约。
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.refresh_token import RefreshToken
from app.services import auth_service


def _account(db):
    user = user_crud.create(
        db,
        phone="13800138999",
        password_hash=hash_password("abc12345"),
        role_default="admin",
    )
    tenant = tenant_crud.create_tenant(db, name="Refresh Token 租户")
    tenant_crud.add_member(db, tenant_id=tenant.id, user_id=user.id, role="admin")
    db.commit()
    return user, tenant


def test_login_issues_hashed_refresh_token(db):
    user, tenant = _account(db)

    result = auth_service.login(db, phone=user.phone, password="abc12345")

    assert result.refresh_token
    stored = db.execute(select(RefreshToken)).scalar_one()
    assert stored.user_id == user.id
    assert stored.tenant_id == tenant.id
    assert stored.status == "active"
    assert stored.token_hash != result.refresh_token
    assert len(stored.token_hash) == 64


def test_refresh_rotates_once_and_replay_revokes_family(db):
    user, _ = _account(db)
    login = auth_service.login(db, phone=user.phone, password="abc12345")

    rotated = auth_service.refresh(db, refresh_token=login.refresh_token)
    assert rotated.refresh_token != login.refresh_token

    with pytest.raises(auth_service.RefreshTokenReuseDetected):
        auth_service.refresh(db, refresh_token=login.refresh_token)

    family = db.execute(
        select(RefreshToken).where(RefreshToken.family_id == rotated.family_id)
    ).scalars().all()
    assert len(family) == 2
    assert not any(token.status == "active" for token in family)


def test_expired_refresh_token_is_rejected(db):
    user, _ = _account(db)
    login = auth_service.login(db, phone=user.phone, password="abc12345")
    stored = db.execute(select(RefreshToken)).scalar_one()
    stored.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(auth_service.InvalidRefreshToken):
        auth_service.refresh(db, refresh_token=login.refresh_token)

    db.refresh(stored)
    assert stored.status == "expired"


def test_logout_revokes_refresh_family(db):
    user, _ = _account(db)
    login = auth_service.login(db, phone=user.phone, password="abc12345")

    auth_service.revoke_refresh_token(db, refresh_token=login.refresh_token)

    stored = db.execute(select(RefreshToken)).scalar_one()
    assert stored.status == "revoked"
    with pytest.raises(auth_service.InvalidRefreshToken):
        auth_service.refresh(db, refresh_token=login.refresh_token)


def test_public_refresh_endpoint_returns_rotated_pair(client, db):
    user, _ = _account(db)
    login = auth_service.login(db, phone=user.phone, password="abc12345")

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login.refresh_token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["refresh_token"] != login.refresh_token


def test_revoke_all_supports_password_change_and_admin_kickout(db):
    user, _ = _account(db)
    first = auth_service.login(db, phone=user.phone, password="abc12345")
    second = auth_service.login(db, phone=user.phone, password="abc12345")

    assert auth_service.revoke_user_refresh_tokens(db, user_id=user.id) == 2
    for raw in (first.refresh_token, second.refresh_token):
        with pytest.raises(auth_service.InvalidRefreshToken):
            auth_service.refresh(db, refresh_token=raw)


def test_password_reset_revokes_existing_refresh_tokens(db, monkeypatch):
    user, _ = _account(db)
    login = auth_service.login(db, phone=user.phone, password="abc12345")
    monkeypatch.setattr(auth_service, "verify_code", lambda *args, **kwargs: True)

    auth_service.reset_password(
        db,
        target=user.phone,
        code="123456",
        new_password="newpass123",
        code_type="sms",
    )

    with pytest.raises(auth_service.InvalidRefreshToken):
        auth_service.refresh(db, refresh_token=login.refresh_token)


def test_logout_endpoint_revokes_refresh_family(client, db):
    user, _ = _account(db)
    login = auth_service.login(db, phone=user.phone, password="abc12345")

    response = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {login.access_token}"},
        json={"refresh_token": login.refresh_token},
    )

    assert response.status_code == 200
    with pytest.raises(auth_service.InvalidRefreshToken):
        auth_service.refresh(db, refresh_token=login.refresh_token)


def test_admin_kickout_endpoint_revokes_target_tenant_tokens(client, db):
    admin, tenant = _account(db)
    target = user_crud.create(
        db,
        phone="13800138888",
        password_hash=hash_password("abc12345"),
        role_default="agent",
    )
    tenant_crud.add_member(db, tenant_id=tenant.id, user_id=target.id, role="agent")
    target_access, target_refresh, _ = auth_service.issue_token_pair(
        db, user_id=target.id, tenant_id=tenant.id, role="agent"
    )
    del target_access
    admin_login = auth_service.login(db, phone=admin.phone, password="abc12345")
    db.commit()

    response = client.post(
        "/api/v1/auth/tokens/revoke-user",
        headers={"Authorization": f"Bearer {admin_login.access_token}"},
        json={"user_id": target.id},
    )

    assert response.status_code == 200
    with pytest.raises(auth_service.InvalidRefreshToken):
        auth_service.refresh(db, refresh_token=target_refresh)
