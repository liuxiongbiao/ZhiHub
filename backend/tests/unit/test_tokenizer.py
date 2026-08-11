"""
backend/tests/unit/test_tokenizer.py

职责: 测试 Token 估算与消息截断（上下文窗口管理）
对应日程: 003-agent-graph

测试内容:
  - TestEstimateTokens: 空串 0 / 英文约 4 chars/token / 中文约 1.5 chars/token / 中英混合
  - TestCountMessagesTokens: 空列表 0 / 单条/多条消息含角色标记开销
  - TestTruncateMessages: 不超预算全保留 / 超预算保留最近 / 预算极紧至少留 1 条
"""

from app.utils.tokenizer import estimate_tokens, count_messages_tokens, truncate_messages


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english_text(self):
        """英文约 4 chars/token."""
        tokens = estimate_tokens("Hello world")
        assert tokens > 0
        assert tokens < len("Hello world")  # 远小于字符数

    def test_chinese_text(self):
        """中文约 1.5 chars/token."""
        tokens = estimate_tokens("你好世界")
        assert tokens > 0
        # 中文密度更高，4 个中文字 → ~2-3 tokens
        assert tokens <= 3

    def test_mixed_text(self):
        """中英混合."""
        tokens = estimate_tokens("Hello 你好")
        assert tokens > 0


class TestCountMessagesTokens:
    def test_empty(self):
        assert count_messages_tokens([]) == 0

    def test_single_message(self):
        msgs = [{"role": "user", "content": "你好"}]
        tokens = count_messages_tokens(msgs)
        assert tokens > 0  # 内容 token + 角色标记

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "如何升级？"},
            {"role": "assistant", "content": "在设置中升级。"},
        ]
        tokens = count_messages_tokens(msgs)
        assert tokens > 0


class TestTruncateMessages:
    def test_empty(self):
        assert truncate_messages([]) == []

    def test_within_budget(self):
        """不超预算时全部保留."""
        msgs = [{"role": "user", "content": "你好"}]
        result = truncate_messages(msgs, max_tokens=1000)
        assert len(result) == len(msgs)

    def test_over_budget_truncates(self):
        """超预算时保留最近的消息."""
        msgs = []
        for i in range(100):
            msgs.append({"role": "user", "content": f"消息 {i} " + "长" * 50})
        result = truncate_messages(msgs, max_tokens=100, keep_recent=2)
        # 应大幅减少
        assert len(result) < len(msgs)
        # T34: 核心语义——保留最近消息（原只断言"变少了"，从中间砍也照样绿）
        assert result[-1]["content"] == msgs[-1]["content"]

    def test_keep_recent_minimum(self):
        """预算极紧时至少保留 1 条消息."""
        msgs = [{"role": "user", "content": "长" * 500} for _ in range(3)]
        result = truncate_messages(msgs, max_tokens=10, keep_recent=2)
        # 每条消息 ~333 tokens 远超 10 token 预算，最终只保留 1 条
        assert len(result) >= 1
