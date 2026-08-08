from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
import logging
import time
from typing import Any, Literal

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

from config.chroma_conf import load_config_from_env
from config.settings import get_settings
from services.bm25_retriever import BM25Hit, BM25Index
from services.reranker import rerank_documents
from utils.observability import log_event, truncate_log_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalOptions:
    mode: Literal["dense", "hybrid"] | None = None
    rerank_enabled: bool | None = None
    dense_candidate_k: int | None = None
    bm25_candidate_k: int | None = None
    rerank_candidate_k: int | None = None
    final_top_k: int | None = None
    score_threshold: float | None = None


@dataclass
class RetrievalResult:
    documents: list[Document]
    mode: str
    rerank_enabled: bool
    highest_dense_score: float | None
    rerank_succeeded: bool
    timings_ms: dict[str, float]
    candidate_counts: dict[str, int]


@dataclass
class _Candidate:
    key: str
    document: Document
    dense_score: float | None = None
    dense_rank: int | None = None
    bm25_score: float | None = None
    bm25_rank: int | None = None
    rrf_score: float | None = None
    pre_rerank_rank: int | None = None
    rerank_score: float | None = None
    final_rank: int | None = None


@dataclass
class _ResolvedOptions:
    mode: Literal["dense", "hybrid"]
    rerank_enabled: bool
    dense_candidate_k: int
    bm25_candidate_k: int
    rerank_candidate_k: int
    final_top_k: int
    score_threshold: float
    rrf_k: int
    rerank_model: str
    rerank_timeout_seconds: float
    rerank_document_max_chars: int
    bm25_k1: float
    bm25_b: float


def _resolve_options(options: RetrievalOptions | None) -> _ResolvedOptions:
    settings = get_settings()
    options = options or RetrievalOptions()
    mode = options.mode or settings.rag_retrieval_mode
    final_top_k = options.final_top_k or settings.rag_final_top_k
    rerank_enabled = (
        settings.rag_rerank_enabled if options.rerank_enabled is None else options.rerank_enabled
    )
    return _ResolvedOptions(
        mode=mode,
        rerank_enabled=rerank_enabled and mode == "hybrid",
        dense_candidate_k=max(
            final_top_k, options.dense_candidate_k or settings.rag_dense_candidate_k
        ),
        bm25_candidate_k=max(
            final_top_k, options.bm25_candidate_k or settings.rag_bm25_candidate_k
        ),
        rerank_candidate_k=max(
            final_top_k, options.rerank_candidate_k or settings.rag_rerank_candidate_k
        ),
        final_top_k=final_top_k,
        score_threshold=(
            settings.rag_score_threshold
            if options.score_threshold is None
            else options.score_threshold
        ),
        rrf_k=settings.rag_rrf_k,
        rerank_model=settings.rag_rerank_model,
        rerank_timeout_seconds=settings.rag_rerank_timeout_seconds,
        rerank_document_max_chars=settings.rag_rerank_document_max_chars,
        bm25_k1=settings.rag_bm25_k1,
        bm25_b=settings.rag_bm25_b,
    )


@lru_cache(maxsize=1)
def get_news_vector_store() -> Chroma:
    settings = get_settings()
    api_key = settings.dashscope_key
    if not api_key:
        raise RuntimeError("服务端未配置 DASHSCOPE_API_KEY，无法初始化检索器")

    chroma_config = load_config_from_env()
    embeddings = DashScopeEmbeddings(
        model=settings.dashscope_embedding_model,
        dashscope_api_key=api_key,
        max_retries=settings.rag_add_retry_attempts,
    )
    return Chroma(
        collection_name=chroma_config.collection_name,
        persist_directory=chroma_config.persist_directory,
        embedding_function=embeddings,
        collection_metadata={"hnsw:space": chroma_config.distance_metric},
    )


def get_collection_runtime_info(vector_store: Chroma | None = None) -> dict[str, Any]:
    store = vector_store or get_news_vector_store()
    collection = store._collection
    configuration = getattr(collection, "configuration", None) or {}
    hnsw_config = configuration.get("hnsw") or {}
    metadata = collection.metadata or {}
    metric = hnsw_config.get("space") or metadata.get("hnsw:space") or "l2"
    return {
        "collection": collection.name,
        "persist_directory": get_settings().chroma_persist_directory,
        "metadata": metadata or None,
        "distance_metric": metric,
        "document_count": collection.count(),
    }


def _document_key(document: Document) -> str:
    metadata = document.metadata
    return f"news-{metadata.get('news_id')}-chunk-{metadata.get('chunk_index', 0)}"


def _load_bm25_documents(vector_store: Chroma) -> list[Document]:
    snapshot = vector_store._collection.get(include=["documents", "metadatas"])
    contents = snapshot.get("documents") or []
    metadatas = snapshot.get("metadatas") or []
    return [
        Document(page_content=content or "", metadata=metadata or {})
        for content, metadata in zip(contents, metadatas)
    ]


@lru_cache(maxsize=1)
def get_news_bm25_index() -> BM25Index:
    settings = get_settings()
    return BM25Index(
        _load_bm25_documents(get_news_vector_store()),
        k1=settings.rag_bm25_k1,
        b=settings.rag_bm25_b,
    )


def clear_retrieval_caches() -> None:
    """Clear cached retrieval objects after a collection is rebuilt in this process."""
    get_news_retriever.cache_clear()
    get_news_bm25_index.cache_clear()
    get_news_vector_store.cache_clear()


@lru_cache(maxsize=1)
def get_news_retriever():
    """Return the V1-compatible dense LangChain Retriever."""
    settings = get_settings()
    return get_news_vector_store().as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"k": settings.rag_top_k, "score_threshold": settings.rag_score_threshold},
    )


def _fuse_candidates(
    dense_results: list[tuple[Document, float]],
    bm25_results: list[BM25Hit],
    rrf_k: int,
) -> list[_Candidate]:
    candidates: dict[str, _Candidate] = {}
    for rank, (document, score) in enumerate(dense_results, start=1):
        key = _document_key(document)
        candidate = candidates.setdefault(key, _Candidate(key=key, document=document))
        candidate.dense_rank = rank
        candidate.dense_score = float(score)
    for hit in bm25_results:
        key = _document_key(hit.document)
        candidate = candidates.setdefault(key, _Candidate(key=key, document=hit.document))
        candidate.bm25_rank = hit.rank
        candidate.bm25_score = float(hit.score)

    for candidate in candidates.values():
        score = 0.0
        if candidate.dense_rank is not None:
            score += 1.0 / (rrf_k + candidate.dense_rank)
        if candidate.bm25_rank is not None:
            score += 1.0 / (rrf_k + candidate.bm25_rank)
        candidate.rrf_score = score

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: (
            -(candidate.rrf_score or 0.0),
            candidate.dense_rank or 10**9,
            candidate.bm25_rank or 10**9,
            candidate.key,
        ),
    )
    for rank, candidate in enumerate(ranked, start=1):
        candidate.pre_rerank_rank = rank
    return ranked


def _dense_candidates(results: list[tuple[Document, float]]) -> list[_Candidate]:
    return [
        _Candidate(
            key=_document_key(document),
            document=document,
            dense_score=float(score),
            dense_rank=rank,
        )
        for rank, (document, score) in enumerate(results, start=1)
    ]


def _candidate_document(candidate: _Candidate) -> Document:
    metadata = {
        **candidate.document.metadata,
        "dense_score": candidate.dense_score,
        "dense_rank": candidate.dense_rank,
        "bm25_score": candidate.bm25_score,
        "bm25_rank": candidate.bm25_rank,
        "rrf_score": candidate.rrf_score,
        "pre_rerank_rank": candidate.pre_rerank_rank,
        "rerank_score": candidate.rerank_score,
        "final_rank": candidate.final_rank,
        # Existing prompt/source consumers expect this key. It remains a cosine score.
        "relevance_score": candidate.dense_score or 0.0,
    }
    return Document(page_content=candidate.document.page_content, metadata=metadata)


def _candidate_log_summary(candidate: _Candidate) -> dict[str, Any]:
    metadata = candidate.document.metadata
    return {
        "news_id": metadata.get("news_id"),
        "title": metadata.get("title"),
        "chunk_index": metadata.get("chunk_index"),
        "dense_rank": candidate.dense_rank,
        "bm25_rank": candidate.bm25_rank,
        "rrf_score": round(candidate.rrf_score, 6) if candidate.rrf_score is not None else None,
        "pre_rerank_rank": candidate.pre_rerank_rank,
        "rerank_score": (
            round(candidate.rerank_score, 6) if candidate.rerank_score is not None else None
        ),
        "final_rank": candidate.final_rank,
    }


async def _run_reranker(
    query: str,
    candidates: list[_Candidate],
    options: _ResolvedOptions,
) -> tuple[list[_Candidate], float, bool]:
    rerank_input = candidates[: options.rerank_candidate_k]
    log_event(
        logger,
        logging.INFO,
        "rag_rerank_started",
        input_count=len(rerank_input),
        model=options.rerank_model,
    )
    started = time.perf_counter()
    try:
        texts = [
            f"标题：{candidate.document.metadata.get('title', '')}\n"
            f"{candidate.document.page_content[: options.rerank_document_max_chars]}"
            for candidate in rerank_input
        ]
        scores = await rerank_documents(
            api_key=get_settings().dashscope_key,
            model=options.rerank_model,
            query=query,
            documents=texts,
            top_n=min(options.final_top_k, len(texts)),
            timeout_seconds=options.rerank_timeout_seconds,
        )
        ranked: list[_Candidate] = []
        seen_indices: set[int] = set()
        for item in scores:
            if 0 <= item.index < len(rerank_input) and item.index not in seen_indices:
                seen_indices.add(item.index)
                candidate = rerank_input[item.index]
                candidate.rerank_score = item.score
                ranked.append(candidate)
        expected_count = min(options.final_top_k, len(rerank_input))
        if len(ranked) < expected_count:
            raise RuntimeError("reranker returned an incomplete result")
        succeeded = True
    except Exception as exc:
        logger.exception("RAG reranker failed; falling back to RRF order")
        log_event(
            logger,
            logging.WARNING,
            "rag_rerank_failed",
            model=options.rerank_model,
            error_type=type(exc).__name__,
            fallback="rrf",
        )
        ranked = rerank_input[: options.final_top_k]
        succeeded = False

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    log_event(
        logger,
        logging.INFO,
        "rag_rerank_completed",
        input_count=len(rerank_input),
        output_count=len(ranked),
        duration_ms=duration_ms,
        model=options.rerank_model,
        success=succeeded,
    )
    return ranked, duration_ms, succeeded


async def retrieve_news(
    query: str,
    options: RetrievalOptions | None = None,
) -> RetrievalResult:
    resolved = _resolve_options(options)
    total_started = time.perf_counter()
    init_started = time.perf_counter()
    vector_store = get_news_vector_store()
    runtime_info = get_collection_runtime_info(vector_store)
    bm25_index = get_news_bm25_index() if resolved.mode == "hybrid" else None
    retriever_init_ms = round((time.perf_counter() - init_started) * 1000, 2)

    log_event(
        logger,
        logging.INFO,
        "rag_retrieval_started",
        query=query,
        mode=resolved.mode,
        rerank_enabled=resolved.rerank_enabled,
        dense_candidate_k=resolved.dense_candidate_k,
        bm25_candidate_k=resolved.bm25_candidate_k if bm25_index else 0,
        final_top_k=resolved.final_top_k,
        score_threshold=resolved.score_threshold,
        threshold_stage="dense_cosine_gate",
        collection=runtime_info["collection"],
        distance_metric=runtime_info["distance_metric"],
    )

    async def run_dense() -> tuple[list[tuple[Document, float]], float]:
        started = time.perf_counter()
        results = await vector_store.asimilarity_search_with_relevance_scores(
            query, k=resolved.dense_candidate_k
        )
        duration = round((time.perf_counter() - started) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "rag_dense_completed",
            candidate_count=len(results),
            duration_ms=duration,
            includes_query_embedding=True,
        )
        return results, duration

    async def run_bm25() -> tuple[list[BM25Hit], float]:
        started = time.perf_counter()
        results = await asyncio.to_thread(bm25_index.search, query, resolved.bm25_candidate_k)
        duration = round((time.perf_counter() - started) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "rag_bm25_completed",
            candidate_count=len(results),
            duration_ms=duration,
        )
        return results, duration

    if bm25_index is not None:
        (dense_results, dense_ms), (bm25_results, bm25_ms) = await asyncio.gather(
            run_dense(), run_bm25()
        )
    else:
        dense_results, dense_ms = await run_dense()
        bm25_results, bm25_ms = [], 0.0

    highest_dense_score = max((float(score) for _, score in dense_results), default=None)
    fusion_ms = 0.0
    if resolved.mode == "dense":
        accepted = [item for item in dense_results if item[1] >= resolved.score_threshold]
        candidates = _dense_candidates(accepted)[: resolved.final_top_k]
    else:
        fusion_started = time.perf_counter()
        candidates = _fuse_candidates(dense_results, bm25_results, resolved.rrf_k)
        fusion_ms = round((time.perf_counter() - fusion_started) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "rag_fusion_completed",
            candidate_count=len(candidates),
            duration_ms=fusion_ms,
            rrf_k=resolved.rrf_k,
        )

    # RRF and reranker scores are not cosine scores. The OOD gate therefore uses
    # only the highest dense cosine relevance score for every pipeline.
    passed_dense_gate = (
        highest_dense_score is not None and highest_dense_score >= resolved.score_threshold
    )
    rerank_ms = 0.0
    rerank_succeeded = False
    if not passed_dense_gate:
        final_candidates: list[_Candidate] = []
    elif resolved.rerank_enabled:
        final_candidates, rerank_ms, rerank_succeeded = await _run_reranker(
            query, candidates, resolved
        )
    else:
        final_candidates = candidates[: resolved.final_top_k]

    for rank, candidate in enumerate(final_candidates, start=1):
        candidate.final_rank = rank
    documents = [_candidate_document(candidate) for candidate in final_candidates]
    retrieval_total_ms = round((time.perf_counter() - total_started) * 1000, 2)
    timings = {
        "retriever_init_ms": retriever_init_ms,
        "dense_ms": dense_ms,
        "bm25_ms": bm25_ms,
        "fusion_ms": fusion_ms,
        "rerank_ms": rerank_ms,
        "retrieval_total_ms": retrieval_total_ms,
    }
    counts = {
        "dense": len(dense_results),
        "bm25": len(bm25_results),
        "fusion": len(candidates) if resolved.mode == "hybrid" else 0,
        "final": len(documents),
    }

    log_event(
        logger,
        logging.INFO,
        "rag_retrieval_completed",
        query=query,
        mode=resolved.mode,
        rerank_enabled=resolved.rerank_enabled,
        rerank_succeeded=rerank_succeeded,
        retrieved_count=len(documents),
        highest_dense_score=(
            round(highest_dense_score, 6) if highest_dense_score is not None else None
        ),
        timings_ms=timings,
        candidate_counts=counts,
        documents=[_candidate_log_summary(candidate) for candidate in final_candidates],
    )

    if not documents:
        log_event(
            logger,
            logging.INFO,
            "rag_no_relevant_documents",
            query=query,
            top_k=resolved.final_top_k,
            threshold=resolved.score_threshold,
            threshold_stage="dense_cosine_gate",
            highest_candidate_score=(
                round(highest_dense_score, 6) if highest_dense_score is not None else None
            ),
            reason=(
                "highest_dense_score_below_threshold"
                if dense_results
                else "vector_store_returned_no_candidates"
            ),
        )

    settings = get_settings()
    if settings.rag_debug_log:
        log_event(
            logger,
            logging.INFO,
            "rag_debug_chunks",
            query=query,
            chunks=[
                {
                    **_candidate_log_summary(candidate),
                    "content": truncate_log_text(
                        candidate.document.page_content, settings.rag_debug_max_chars
                    ),
                }
                for candidate in final_candidates
            ],
        )

    return RetrievalResult(
        documents=documents,
        mode=resolved.mode,
        rerank_enabled=resolved.rerank_enabled,
        highest_dense_score=highest_dense_score,
        rerank_succeeded=rerank_succeeded,
        timings_ms=timings,
        candidate_counts=counts,
    )


async def retrieve_news_documents(query: str) -> list[Document]:
    """Compatibility wrapper retained for existing callers."""
    return (await retrieve_news(query)).documents
