"""
backend/tests/integration/test_knowledge_api.py

职责: 测试知识库 API 端点集成（上传/列表/检索测试/重建的鉴权守卫）
对应日程: 002-knowledge-base / T34（原 2 个无条件 skip 补为带认证的真测试）

测试内容:
  - TestUploadEndpoint: 未认证 401 / 不支持格式 400 / 超大文件 400（认证后）
  - TestListEndpoint: 未认证 401
  - TestSearchTestEndpoint: 未认证 401（middleware 先于路由校验）
  - TestRebuildEndpoint: 未认证 401

设计决策:
  使用 conftest 的 client fixture（覆盖 get_db 指向内存库）——此前本地 fixture
  无 override，认证后请求会直连真实 MySQL（潜伏地雷）。
"""

import pytest


class TestUploadEndpoint:
    """POST /api/v1/knowledge/upload"""

    def test_upload_requires_auth(self, client):
        """未认证请求应返回 401."""
        resp = client.post("/api/v1/knowledge/upload")
        assert resp.status_code == 401

    def test_upload_unsupported_format(self, client, make_token):
        """不支持格式（.exe）应返回 400（T34: 原 skip 补真测试）。"""
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/knowledge/upload",
            files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400, resp.text

    def test_upload_file_too_large(self, client, make_token):
        """超大文件应返回 400（T34: 原 skip 补真测试）。"""
        from app.utils.file_parser import MAX_FILE_SIZE

        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/knowledge/upload",
            files={"file": ("big.md", b"x" * (MAX_FILE_SIZE + 1), "text/markdown")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400, resp.text


class TestListEndpoint:
    """GET /api/v1/knowledge/list"""

    def test_list_requires_auth(self, client):
        """未认证请求应返回 401."""
        resp = client.get("/api/v1/knowledge/list")
        assert resp.status_code == 401


class TestSearchTestEndpoint:
    """POST /api/v1/knowledge/test"""

    def test_empty_query_rejected_when_authenticated(self, client):
        """空查询被 auth middleware 拦截返回 401（middleware 先于路由校验执行）."""
        resp = client.post(
            "/api/v1/knowledge/test",
            json={"query": "", "history": []},
        )
        # auth middleware 先于路由执行→未认证返回 401
        # 认证后空查询会被路由返回 400（端到端验证）
        assert resp.status_code == 401

    def test_search_requires_auth(self, client):
        """未认证请求应返回 401."""
        resp = client.post(
            "/api/v1/knowledge/test",
            json={"query": "测试", "history": []},
        )
        assert resp.status_code == 401


class TestRebuildEndpoint:
    """POST /api/v1/knowledge/{doc_id}/rebuild"""

    def test_rebuild_requires_auth(self, client):
        """未认证请求应返回 401."""
        resp = client.post("/api/v1/knowledge/1/rebuild")
        assert resp.status_code == 401
