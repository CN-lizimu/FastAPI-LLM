import json
import logging

import pytest

from cache import news_cache
from evaluation.compare_chroma_metrics import _relevance_score
from services.retriever_factory import get_collection_runtime_info
from utils.observability import log_event, reset_trace_id, set_trace_id, truncate_log_text


class FakeCollection:
    name = "candidate"
    metadata = {"hnsw:space": "cosine"}
    configuration = {"hnsw": {"space": "cosine"}}

    @staticmethod
    def count():
        return 403


class FakeVectorStore:
    _collection = FakeCollection()


def test_collection_runtime_info_reports_actual_metric():
    info = get_collection_runtime_info(FakeVectorStore())
    assert info["collection"] == "candidate"
    assert info["distance_metric"] == "cosine"
    assert info["document_count"] == 403


def test_structured_log_uses_trace_id_and_truncates(caplog):
    logger = logging.getLogger("test.rag.observability")
    token = set_trace_id("trace-rag-123")
    try:
        with caplog.at_level(logging.INFO):
            log_event(logger, logging.INFO, "rag_test_event", query="新闻问题")
    finally:
        reset_trace_id(token)

    payload = json.loads(caplog.records[-1].message)
    assert payload["trace_id"] == "trace-rag-123"
    assert payload["event"] == "rag_test_event"
    assert truncate_log_text("abcdefgh", 5).startswith("abcde")


def test_metric_relevance_conversion_matches_langchain_rules():
    assert _relevance_score("cosine", 0.2) == pytest.approx(0.8)
    assert _relevance_score("l2", 0.0) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_view_update_invalidation_deletes_related_cache_groups(monkeypatch):
    deleted_keys = []
    deleted_patterns = []

    async def fake_delete(*keys):
        deleted_keys.extend(keys)
        return len(keys)

    async def fake_delete_pattern(pattern):
        deleted_patterns.append(pattern)
        return 1

    monkeypatch.setattr(news_cache, "delete_cache", fake_delete)
    monkeypatch.setattr(news_cache, "delete_cache_pattern", fake_delete_pattern)

    deleted = await news_cache.invalidate_news_after_view_update(news_id=8, category_id=3)
    assert deleted == 3
    assert deleted_keys == ["news:detail:8"]
    assert deleted_patterns == ["news_list:3:*", "news:related:*:3"]
