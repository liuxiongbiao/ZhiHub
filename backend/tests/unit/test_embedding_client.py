"""
backend/tests/unit/test_embedding_client.py

职责: 验证大文档向量化分批并发策略不会打乱切片与向量的对应顺序。
"""

import threading
import time

import httpx

from app.core import embedding_client


def test_embed_batches_runs_concurrently_and_preserves_order(monkeypatch):
    """多批请求应并发执行，最终向量仍严格按输入顺序返回。"""
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_embed_texts(texts):
        """以短暂停顿模拟外部请求，并返回可识别的顺序标记。"""
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        result = [[float(text)] for text in texts]
        with lock:
            active -= 1
        return result

    monkeypatch.setattr(embedding_client, "embed_texts", fake_embed_texts)
    values = [str(index) for index in range(embedding_client.BATCH_SIZE * 3)]

    result = embedding_client.embed_batches(values)

    assert max_active >= 2
    assert [int(vector[0]) for vector in result] == list(range(len(values)))


def test_embed_batches_empty_input_returns_empty(monkeypatch):
    """空输入不创建线程池，也不调用外部向量服务。"""
    monkeypatch.setattr(
        embedding_client,
        "embed_texts",
        lambda texts: (_ for _ in ()).throw(AssertionError("不应调用外部服务")),
    )
    assert embedding_client.embed_batches([]) == []


def test_embed_texts_respects_retry_after_on_rate_limit(monkeypatch):
    """429 时按 Retry-After 冷却后重试，避免大文档持续撞限流。"""
    calls = 0
    waits: list[float] = []

    def fake_post(*args, **kwargs):
        """第一次返回 429，第二次返回一条合法向量。"""
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://example.test/embeddings")
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}]},
            request=request,
        )

    monkeypatch.setattr(embedding_client.httpx, "post", fake_post)
    monkeypatch.setattr(embedding_client.time, "sleep", waits.append)

    assert embedding_client.embed_texts(["文本"]) == [[0.1, 0.2]]
    assert calls == 2
    assert waits == [7.0]
