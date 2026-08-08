from __future__ import annotations

import pytest
from langchain_core.documents import Document

from services.bm25_retriever import BM25Index, tokenize_chinese_news
from services.get_retrievel import needs_query_rewrite
from services.retriever_factory import (
    RetrievalOptions,
    _dense_candidates,
    _fuse_candidates,
    _resolve_options,
    _run_reranker,
)


def _document(news_id: int, title: str, content: str, chunk_index: int = 0) -> Document:
    return Document(
        page_content=content,
        metadata={"news_id": news_id, "chunk_index": chunk_index, "title": title},
    )


def test_chinese_tokenizer_uses_character_bigrams_and_keeps_ascii_terms():
    tokens = tokenize_chinese_news("C919国产大飞机完成演示飞行")
    assert "c919" in tokens
    assert "国产" in tokens
    assert "飞机" in tokens
    assert "飞行" in tokens


def test_bm25_ranks_matching_chinese_news_first():
    index = BM25Index(
        [
            _document(1, "量子计算取得突破", "我国科学家完成量子计算实验"),
            _document(2, "水利投资增长", "全国水利工程建设加快"),
        ]
    )
    hits = index.search("量子计算有什么突破", k=2)
    assert hits
    assert hits[0].document.metadata["news_id"] == 1
    assert hits[0].score > 0


def test_rrf_fuses_ranks_without_adding_raw_scores():
    dense_first = _document(1, "Dense first", "dense")
    lexical_first = _document(2, "BM25 first", "bm25")
    bm25_index = BM25Index([lexical_first, dense_first])
    bm25_hits = bm25_index.search("bm25", k=2)

    fused = _fuse_candidates(
        [(dense_first, 0.91), (lexical_first, 0.70)],
        bm25_hits,
        rrf_k=60,
    )

    by_id = {candidate.document.metadata["news_id"]: candidate for candidate in fused}
    assert by_id[1].dense_rank == 1
    assert by_id[2].bm25_rank == 1
    assert by_id[2].rrf_score == pytest.approx(1 / 62 + 1 / 61)


def test_dense_candidates_do_not_claim_rrf_or_bm25_metadata():
    first = _document(1, "Dense first", "dense")
    candidates = _dense_candidates([(first, 0.91)])

    assert candidates[0].dense_rank == 1
    assert candidates[0].bm25_rank is None
    assert candidates[0].rrf_score is None
    assert candidates[0].pre_rerank_rank is None


def test_independent_question_skips_rewrite_even_with_history():
    history = [object(), object()]
    query = "中国在经济方面有什么新闻"
    assert needs_query_rewrite(query, history) is False


def test_context_reference_requires_rewrite_when_history_exists():
    history = [object(), object()]
    assert needs_query_rewrite("这个项目后来有什么进展？", history) is True
    assert needs_query_rewrite("它取得了什么成果？", history) is True


def test_context_reference_without_history_cannot_be_rewritten():
    assert needs_query_rewrite("它取得了什么成果？", []) is False


@pytest.mark.asyncio
async def test_reranker_failure_falls_back_to_rrf_order(monkeypatch):
    first = _document(1, "第一条", "内容一")
    second = _document(2, "第二条", "内容二")
    candidates = _fuse_candidates([(first, 0.9), (second, 0.8)], [], rrf_k=60)

    async def fail_rerank(**kwargs):
        raise TimeoutError("upstream timeout")

    monkeypatch.setattr("services.retriever_factory.rerank_documents", fail_rerank)
    ranked, _, succeeded = await _run_reranker(
        "测试问题",
        candidates,
        _resolve_options(RetrievalOptions(mode="hybrid", rerank_enabled=True)),
    )

    assert succeeded is False
    assert [item.key for item in ranked] == [item.key for item in candidates[:5]]
