"""
backend/app/utils/tokenizer.py

职责: 估算文本 token 数量，用于 agent_service 上下文截断预算判断

核心设计:
  轻量近似：中文约 1.5 chars/token，其他约 4 chars/token，误差 ±20% 对截断判断足够。
  生产环境应替换为 tiktoken 精确计数，demo 阶段近似即可。
"""

import re

# 中文/日文/韩文等 CJK 字符的 token 密度更高
_CJK_PATTERN = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
# Token 预算（与 BUILD_PLAN 对齐）
MAX_TOKEN_BUDGET = 4000


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。

    中文约 1.5 字符/token，其他约 4 字符/token。
    误差约 ±20%，对截断判断足够。
    """
    if not text:
        return 0

    cjk_chars = len(_CJK_PATTERN.findall(text))
    other_chars = len(text) - cjk_chars
    return int(cjk_chars / 1.5 + other_chars / 4)


def count_messages_tokens(messages: list[dict]) -> int:
    """计算消息列表的总 token 数。

    Args:
        messages: [{"role": "user", "content": "..."}, ...]
    """
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        # 每条消息的角色标记约 2 tokens
        total += 2
    return total


def truncate_messages(
    messages: list[dict], max_tokens: int = MAX_TOKEN_BUDGET, keep_recent: int = 10
) -> list[dict]:
    """截断消息列表，保留最近 N 条且总 token 数不超过预算。

    策略: 从最早的消息开始丢弃，保留至少 keep_recent 条。
    如果保留最近 N 条仍超预算，继续从早期丢弃直到满足预算或只剩 1 条。
    """
    if not messages:
        return []

    # 保存至少 keep_recent 条
    recent = messages[-keep_recent:]

    # 如果最近 N 条已超预算，从早期继续丢弃
    while count_messages_tokens(recent) > max_tokens and len(recent) > 1:
        recent = recent[-(len(recent) - 1):]

    return recent
