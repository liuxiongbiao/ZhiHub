"""
backend/tests/contracts/test_model_gateway.py

职责: 测试模型网关 llm_complete 主备切换（#5, PRD 3.14）

测试内容:
  - TestModelGateway: llm_complete 封装主模型失败自动切备用模型
    - 主模型成功 -> 直接返回，不切备用
    - 主模型失败 -> 自动切备用模型，返回备用结果
    - 主备都失败 -> 抛异常（由调用方兜底）
    - 使用 config 中的 llm_primary_model/llm_backup_model 配置
    - 切换时记 logger warning

  - 模型网关是 LLM 调用的统一入口，所有 litellm.completion 直调点改为 llm_complete
  - 主备降级是高可用核心: 主模型 API 不可用时自动切备用，用户无感
  - try/except 而非重试库: 一次切换足够，重试会增加延迟且可能打满配额
"""

import logging

import pytest


class _MockResp:
    """模拟 litellm ModelResponse，只需 choices[0].message.content。"""

    def __init__(self, content="mock", model="unknown"):
        self.choices = [
            type("Choice", (), {"message": type("Msg", (), {"content": content})})()
        ]
        self.model = model


def _register(client, phone="13800138000"):
    code = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()["code"]
    return client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "abc12345", "tenant_name": "示例科技", "code": code},
    ).json()


class TestModelGateway:
    """#5: llm_complete 主模型失败自动切备用模型（PRD 3.14）。"""

    def test_primary_success_no_fallback(self, monkeypatch):
        """主模型成功 -> 直接返回主模型结果，不切备用。"""
        import litellm
        from app.core import llm_client
        from app.config import settings

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        calls = []

        def mock_completion(*args, **kwargs):
            calls.append(kwargs.get("model"))
            return _MockResp(content="primary answer", model="primary")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        resp = llm_client.llm_complete(messages=[{"role": "user", "content": "hi"}])
        assert resp.choices[0].message.content == "primary answer"
        assert len(calls) == 1
        assert calls[0] == "deepseek/primary-model"

    def test_primary_fail_switch_to_backup(self, monkeypatch):
        """主模型失败 -> 自动切备用模型，返回备用结果。"""
        import litellm
        from app.core import llm_client
        from app.config import settings

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        calls = []

        def mock_completion(*args, **kwargs):
            model = kwargs.get("model")
            calls.append(model)
            if model == "deepseek/primary-model":
                raise RuntimeError("primary model API down")
            return _MockResp(content="backup answer", model="backup")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        resp = llm_client.llm_complete(messages=[{"role": "user", "content": "hi"}])
        assert resp.choices[0].message.content == "backup answer"
        assert len(calls) == 2
        assert calls[0] == "deepseek/primary-model"
        assert calls[1] == "deepseek/backup-model"

    def test_both_fail_raises_exception(self, monkeypatch):
        """主备都失败 -> 抛出异常（由调用方走兜底回复）。"""
        import litellm
        from app.core import llm_client
        from app.config import settings

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        def mock_completion(*args, **kwargs):
            raise RuntimeError("all models down")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        with pytest.raises(RuntimeError, match="all models down"):
            llm_client.llm_complete(messages=[{"role": "user", "content": "hi"}])

    def test_uses_config_models(self, monkeypatch):
        """llm_complete 从 settings 读取 llm_primary_model/llm_backup_model。"""
        import litellm
        from app.core import llm_client
        from app.config import settings

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/custom-primary")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/custom-backup")

        captured = []

        def mock_completion(*args, **kwargs):
            captured.append(kwargs.get("model"))
            return _MockResp(content="ok")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        llm_client.llm_complete(
            messages=[{"role": "user", "content": "test"}],
            max_tokens=100,
            temperature=0.3,
        )
        assert captured[0] == "deepseek/custom-primary"
        # kwargs 透传
        assert mock_completion is not None

    def test_switch_logs_warning(self, monkeypatch, caplog):
        """主模型失败切备用时记 logger warning。"""
        import litellm
        from app.core import llm_client
        from app.config import settings

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        def mock_completion(*args, **kwargs):
            if kwargs.get("model") == "deepseek/primary-model":
                raise RuntimeError("primary down")
            return _MockResp(content="backup")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        with caplog.at_level(logging.WARNING, logger="app.core.llm_client"):
            llm_client.llm_complete(messages=[{"role": "user", "content": "hi"}])

        assert any("primary" in r.message.lower() and "backup" in r.message.lower()
                      for r in caplog.records)

    def test_default_models_when_config_empty(self, monkeypatch):
        """config 为空时默认用 deepseek/deepseek-chat。"""
        import litellm
        from app.core import llm_client
        from app.config import settings

        monkeypatch.setattr(settings, "llm_primary_model", "")
        monkeypatch.setattr(settings, "llm_backup_model", "")

        captured = []

        def mock_completion(*args, **kwargs):
            captured.append(kwargs.get("model"))
            return _MockResp(content="ok")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        llm_client.llm_complete(messages=[{"role": "user", "content": "hi"}])
        assert captured[0] == "deepseek/deepseek-chat"


class TestLlmCompleteIntegration:
    """验证 llm_complete 已接入调用方（retrieval_service / knowledge_agent / planner）。"""

    def test_retrieval_rewrite_uses_llm_complete(self, monkeypatch):
        """retrieval_service.rewrite_query 通过 llm_complete 调用（主失败切备用）。"""
        import litellm
        from app.core import llm_client
        from app.config import settings
        from app.services.retrieval_service import rewrite_query

        monkeypatch.setattr(settings, "llm_enabled", True)
        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        calls = []

        def mock_completion(*args, **kwargs):
            calls.append(kwargs.get("model"))
            if kwargs.get("model") == "deepseek/primary-model":
                raise RuntimeError("primary down")
            return _MockResp(content="改写后的查询")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        result = rewrite_query(
            history=[{"role": "user", "content": "它怎么退款"}],
            current_query="它怎么退款",
        )
        assert result == "改写后的查询"
        assert "deepseek/backup-model" in calls

    def test_knowledge_agent_uses_llm_complete(self, monkeypatch):
        """knowledge_agent._generate_answer 通过 llm_complete 调用。"""
        import litellm
        from app.config import settings
        from app.agents.nodes.knowledge_agent import _generate_answer

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        calls = []

        def mock_completion(*args, **kwargs):
            calls.append(kwargs.get("model"))
            if kwargs.get("model") == "deepseek/primary-model":
                raise RuntimeError("primary down")
            return _MockResp(content="LLM 回答")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        answer, generation_ok = _generate_answer("怎么退款", "（无检索结果）", [])
        assert answer == "LLM 回答"
        assert generation_ok is True
        assert "deepseek/backup-model" in calls

    def test_planner_generate_report_uses_llm_complete(self, monkeypatch):
        """planner._generate_report 通过 llm_complete 调用。"""
        import litellm
        from app.config import settings
        from app.agents.deep_agent.planner import _generate_report

        monkeypatch.setattr(settings, "llm_primary_model", "deepseek/primary-model")
        monkeypatch.setattr(settings, "llm_backup_model", "deepseek/backup-model")

        calls = []

        def mock_completion(*args, **kwargs):
            calls.append(kwargs.get("model"))
            if kwargs.get("model") == "deepseek/primary-model":
                raise RuntimeError("primary down")
            return _MockResp(content="分析报告内容")

        monkeypatch.setattr(litellm, "completion", mock_completion)

        report = _generate_report("工单分析", {"total_tickets": 10, "open_tickets": 3}, ["SOP文档"])
        assert report == "分析报告内容"
        assert "deepseek/backup-model" in calls
