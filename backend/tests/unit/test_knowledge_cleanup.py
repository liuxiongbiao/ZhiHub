"""知识管道补偿清理必须同步提交 Milvus 删除，防止测试/失败数据污染检索。"""

from app.tasks import knowledge_tasks


def test_compensate_cleanup_flushes_milvus_and_refreshes_es(monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeMilvus:
        def delete(self, *, expr: str):
            calls.append(("milvus_delete", expr))

        def flush(self):
            calls.append(("milvus_flush",))

    class FakeEs:
        def delete_by_query(self, **kwargs):
            calls.append(("es_delete", kwargs))

    monkeypatch.setattr(knowledge_tasks, "get_milvus_collection", lambda: FakeMilvus())
    monkeypatch.setattr(knowledge_tasks, "get_es_client", lambda: FakeEs())

    knowledge_tasks._compensate_vector_cleanup(151)

    assert calls[0] == ("milvus_delete", "doc_id == 151")
    assert calls[1] == ("milvus_flush",)
    assert calls[2][0] == "es_delete"
    assert calls[2][1]["body"] == {"query": {"term": {"doc_id": 151}}}
    assert calls[2][1]["refresh"] is True
