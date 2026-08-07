from functools import lru_cache
import logging
import time
from typing import Any

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

from config.chroma_conf import load_config_from_env
from config.settings import get_settings
from utils.observability import log_event, truncate_log_text


logger = logging.getLogger(__name__)


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


def _document_log_summary(document: Document, score: float, accepted: bool) -> dict[str, Any]:
    metadata = document.metadata
    return {
        "news_id": metadata.get("news_id"),
        "title": metadata.get("title"),
        "chunk_index": metadata.get("chunk_index"),
        "relevance_score": round(float(score), 4),
        "published_at": metadata.get("publish_time"),
        "source": metadata.get("source"),
        "accepted": accepted,
    }


@lru_cache(maxsize=1)
def get_news_retriever():
    """Return a cached Retriever for components that consume LangChain Runnable APIs."""
    settings = get_settings()
    return get_news_vector_store().as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={
            "k": settings.rag_top_k,
            "score_threshold": settings.rag_score_threshold,
        },
    )


async def retrieve_news_documents(query: str) -> list[Document]:
    """Retrieve relevant chunks and retain normalized relevance scores in metadata."""
    settings = get_settings()
    vector_store = get_news_vector_store()
    runtime_info = get_collection_runtime_info(vector_store)
    log_event(
        logger,
        logging.INFO,
        "rag_retrieval_started",
        query=query,
        top_k=settings.rag_top_k,
        score_threshold=settings.rag_score_threshold,
        collection=runtime_info["collection"],
        distance_metric=runtime_info["distance_metric"],
    )

    started = time.perf_counter()
    results = await vector_store.asimilarity_search_with_relevance_scores(
        query,
        k=settings.rag_top_k,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    documents: list[Document] = []
    document_summaries = []
    for document, score in results:
        accepted = score >= settings.rag_score_threshold
        document_summaries.append(_document_log_summary(document, score, accepted))
        if not accepted:
            continue
        document.metadata = {**document.metadata, "relevance_score": float(score)}
        documents.append(document)

    log_event(
        logger,
        logging.INFO,
        "rag_retrieval_completed",
        query=query,
        retrieval_duration_ms=duration_ms,
        candidate_count=len(results),
        retrieved_count=len(documents),
        documents=document_summaries,
    )

    if not documents:
        highest_score = max((float(score) for _, score in results), default=None)
        log_event(
            logger,
            logging.INFO,
            "rag_no_relevant_documents",
            query=query,
            top_k=settings.rag_top_k,
            threshold=settings.rag_score_threshold,
            highest_candidate_score=round(highest_score, 4) if highest_score is not None else None,
            reason="all_candidates_below_threshold" if results else "vector_store_returned_no_candidates",
        )

    if settings.rag_debug_log:
        log_event(
            logger,
            logging.INFO,
            "rag_debug_chunks",
            query=query,
            chunks=[
                {
                    **_document_log_summary(document, score, score >= settings.rag_score_threshold),
                    "content": truncate_log_text(document.page_content, settings.rag_debug_max_chars),
                }
                for document, score in results
            ],
        )
    return documents
