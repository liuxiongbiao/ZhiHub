"""
backend/tests/contracts/test_notifications.py

职责: 站内信闭环。
契约:
  - 创建站内信 -> GET list 可见 / unread-count / mark_read 后已读
  - 配额预警任务触发 -> 写库一条 quota_warning 通知
"""

from app.services.notification_service import create_notification


class TestNotifications:
    def test_create_list_unread_markread(self, client, db, make_token):
        create_notification(
            db, tenant_id=1, title="配额预警：80%",
            content="当前用量 80/100", type="quota_warning",
        )
        token = make_token(user_id=1, tenant_id=1, role="admin")
        headers = {"Authorization": f"Bearer {token}"}

        # 列表
        listed = client.get("/api/v1/notifications", headers=headers).json()
        assert listed["total"] == 1
        assert listed["items"][0]["title"] == "配额预警：80%"
        # 未读数
        unread = client.get("/api/v1/notifications/unread-count", headers=headers).json()
        assert unread["count"] == 1
        # 已读
        nid = listed["items"][0]["id"]
        mark = client.post(f"/api/v1/notifications/{nid}/read", headers=headers)
        assert mark.status_code == 200
        unread2 = client.get("/api/v1/notifications/unread-count", headers=headers).json()
        assert unread2["count"] == 0

    def test_unread_filter(self, client, db, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        headers = {"Authorization": f"Bearer {token}"}
        create_notification(db, tenant_id=1, title="已读", type="system")
        # 标记第一条已读
        listed = client.get("/api/v1/notifications", headers=headers).json()
        client.post(f"/api/v1/notifications/{listed['items'][0]['id']}/read", headers=headers)
        # 再建一条未读
        create_notification(db, tenant_id=1, title="未读", type="handoff")
        unread = client.get("/api/v1/notifications?unread=1", headers=headers).json()
        assert unread["total"] == 1
        assert unread["items"][0]["title"] == "未读"

    def test_quota_warning_task_writes_notification(self, monkeypatch, db, fake_redis):
        from app.tasks.notification_tasks import notify_quota_warning
        from app.core import database as db_module
        monkeypatch.setattr(db_module, "SessionLocal", lambda: db)
        notify_quota_warning(tenant_id=1, level="80%", count=80, quota=100)
        from app.models.notification import Notification
        n = db.query(Notification).filter_by(tenant_id=1, type="quota_warning").first()
        assert n is not None
        assert "80%" in n.title
