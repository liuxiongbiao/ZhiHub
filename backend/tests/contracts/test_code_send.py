"""
backend/tests/contracts/test_code_send.py

职责: 通用验证码发送端点 POST /auth/code/send（target + code_type）
      —— 支撑个人中心邮箱绑定（email 验证码）与密码重置（sms 验证码），
      /sms/send 保持兼容（仅 phone）。

契约:
  POST /auth/code/send {"target": "138...", "code_type": "sms"}   -> 200 + code
  POST /auth/code/send {"target": "a@b.com", "code_type": "email"} -> 200 + code
  频率超限 -> 429
"""


class TestCodeSend:
    def test_send_sms_code(self, client):
        resp = client.post("/api/v1/auth/code/send", json={"target": "13800138111", "code_type": "sms"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["code"]) == 6

    def test_send_email_code(self, client):
        resp = client.post("/api/v1/auth/code/send", json={"target": "bind@example.com", "code_type": "email"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["code"]) == 6

    def test_code_type_defaults_to_sms(self, client):
        resp = client.post("/api/v1/auth/code/send", json={"target": "13800138112"})
        assert resp.status_code == 200

    def test_rate_limited(self, client, fake_redis):
        # 先清频率限制，发送一次后第二次 → 429
        client.post("/api/v1/auth/code/send", json={"target": "13800138113", "code_type": "sms"})
        resp = client.post("/api/v1/auth/code/send", json={"target": "13800138113", "code_type": "sms"})
        assert resp.status_code == 429
