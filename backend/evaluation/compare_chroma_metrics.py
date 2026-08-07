from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from langchain_community.embeddings import DashScopeEmbeddings

from config.settings import get_settings


BASE_DIR = Path(__file__).resolve().parent


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(collection) -> str:
    configuration = getattr(collection, "configuration", None) or {}
    hnsw = configuration.get("hnsw") or {}
    metadata = collection.metadata or {}
    return hnsw.get("space") or metadata.get("hnsw:space") or "l2"


def _relevance_score(metric: str, distance: float) -> float:
    """Match the relevance conversion used by langchain-community Chroma."""
    if metric == "cosine":
        return 1.0 - distance
    if metric == "l2":
        return 1.0 - distance / math.sqrt(2)
    if metric == "ip":
        return 1.0 - distance if distance > 0 else -distance
    raise ValueError(f"不支持的距离度量: {metric}")


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "mean": None, "median": None, "max": None}
    return {
        "min": round(min(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "max": round(max(values), 4),
    }


def _query_collection(collection, embedding: list[float], top_k: int) -> tuple[list[dict], float]:
    started = time.perf_counter()
    result = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    metric = _metric(collection)
    rows = []
    for row_id, metadata, document, distance in zip(
        result["ids"][0],
        result["metadatas"][0],
        result["documents"][0],
        result["distances"][0],
    ):
        score = _relevance_score(metric, float(distance))
        rows.append(
            {
                "id": row_id,
                "news_id": metadata.get("news_id"),
                "title": metadata.get("title"),
                "chunk_index": metadata.get("chunk_index"),
                "published_at": metadata.get("publish_time"),
                "source": metadata.get("source"),
                "distance": round(float(distance), 6),
                "relevance_score": round(score, 6),
                "content_preview": (document or "")[:160],
            }
        )
    return rows, latency_ms


def _recall(rows: list[dict], expected_document_id: int, k: int) -> int:
    return int(expected_document_id in [row["news_id"] for row in rows[:k]])


def _summarize_collection(
    collection,
    normal_results: list[dict],
    broad_results: list[dict],
    ood_results: list[dict],
    thresholds: list[float],
) -> dict[str, Any]:
    raw_recall = {
        f"recall_at_{k}": round(
            statistics.fmean(
                _recall(item["documents"], item["expected_document_id"], k)
                for item in normal_results
            ),
            4,
        )
        for k in (1, 3, 5)
    }
    threshold_results = {}
    for threshold in thresholds:
        normal_filtered = [
            {
                **item,
                "filtered_documents": [
                    row for row in item["documents"] if row["relevance_score"] >= threshold
                ],
            }
            for item in normal_results
        ]
        ood_filtered = [
            [row for row in item["documents"] if row["relevance_score"] >= threshold]
            for item in ood_results
        ]
        broad_filtered = [
            [row for row in item["documents"] if row["relevance_score"] >= threshold]
            for item in broad_results
        ]
        threshold_results[str(threshold)] = {
            **{
                f"recall_at_{k}": round(
                    statistics.fmean(
                        _recall(item["filtered_documents"], item["expected_document_id"], k)
                        for item in normal_filtered
                    ),
                    4,
                )
                for k in (1, 3, 5)
            },
            "ood_rejection_accuracy": round(
                statistics.fmean(int(not rows) for rows in ood_filtered),
                4,
            ),
            "normal_queries_without_context": sum(
                int(not item["filtered_documents"]) for item in normal_filtered
            ),
            "ood_queries_with_context": sum(int(bool(rows)) for rows in ood_filtered),
            "broad_query_acceptance_accuracy": round(
                statistics.fmean(int(bool(rows)) for rows in broad_filtered),
                4,
            ),
            "broad_queries_without_context": sum(int(not rows) for rows in broad_filtered),
        }

    normal_highest = [item["highest_relevance_score"] for item in normal_results]
    broad_highest = [item["highest_relevance_score"] for item in broad_results]
    ood_highest = [item["highest_relevance_score"] for item in ood_results]
    all_results = normal_results + broad_results + ood_results
    all_latencies = [item["retrieval_latency_ms"] for item in all_results]
    all_embedding_latencies = [item["embedding_latency_ms"] for item in all_results]
    return {
        "collection": collection.name,
        "metadata": collection.metadata,
        "configuration": collection.configuration,
        "distance_metric": _metric(collection),
        "document_count": collection.count(),
        "raw_recall": raw_recall,
        "normal_highest_score_distribution": _distribution(normal_highest),
        "broad_highest_score_distribution": _distribution(broad_highest),
        "ood_highest_score_distribution": _distribution(ood_highest),
        "average_highest_relevance_score": round(statistics.fmean(normal_highest), 4),
        "mean_vector_store_latency_ms": round(statistics.fmean(all_latencies), 2),
        "mean_embedding_latency_ms": round(statistics.fmean(all_embedding_latencies), 2),
        "mean_end_to_end_retrieval_latency_ms": round(
            statistics.fmean(
                item["retrieval_latency_ms"] + item["embedding_latency_ms"]
                for item in all_results
            ),
            2,
        ),
        "threshold_results": threshold_results,
    }


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Compare L2 and cosine Chroma retrieval on normal and OOD cases.")
    parser.add_argument("--normal-cases", type=Path, default=BASE_DIR / "rag_cases.json")
    parser.add_argument("--broad-cases", type=Path, default=BASE_DIR / "rag_broad_cases.json")
    parser.add_argument("--ood-cases", type=Path, default=BASE_DIR / "rag_ood_cases.json")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "metric_comparison_results.json")
    parser.add_argument("--collections", nargs="+", default=[
        settings.chroma_legacy_collection_name,
        settings.chroma_cosine_candidate_collection_name,
    ])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--thresholds", nargs="+", type=float, default=[0.2, 0.3, 0.4, 0.5])
    args = parser.parse_args()

    if not settings.dashscope_key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法生成评测查询向量")

    client = chromadb.PersistentClient(path=settings.chroma_persist_directory)
    available = {collection.name for collection in client.list_collections()}
    missing = [name for name in args.collections if name not in available]
    if missing:
        raise RuntimeError(f"评测集合不存在: {missing}")
    collections = {name: client.get_collection(name) for name in args.collections}

    embedding_model = DashScopeEmbeddings(
        model=settings.dashscope_embedding_model,
        dashscope_api_key=settings.dashscope_key,
        max_retries=settings.rag_add_retry_attempts,
    )
    normal_cases = _load_cases(args.normal_cases)
    broad_cases = _load_cases(args.broad_cases)
    ood_cases = _load_cases(args.ood_cases)
    detailed = {name: {"normal": [], "broad": [], "ood": []} for name in args.collections}

    for case_type, cases in (("normal", normal_cases), ("broad", broad_cases), ("ood", ood_cases)):
        for case in cases:
            embedding_started = time.perf_counter()
            embedding = await embedding_model.aembed_query(case["question"])
            embedding_latency_ms = round((time.perf_counter() - embedding_started) * 1000, 2)
            for name, collection in collections.items():
                rows, retrieval_latency_ms = _query_collection(collection, embedding, args.top_k)
                case_result = {
                    **case,
                    "embedding_latency_ms": embedding_latency_ms,
                    "retrieval_latency_ms": retrieval_latency_ms,
                    "highest_relevance_score": rows[0]["relevance_score"] if rows else None,
                    "documents": rows,
                }
                if case_type == "ood":
                    case_result["threshold_evaluations"] = {
                        str(threshold): {
                            "threshold": threshold,
                            "retrieved_count_after_filter": sum(
                                row["relevance_score"] >= threshold for row in rows
                            ),
                            "correctly_rejected": not any(
                                row["relevance_score"] >= threshold for row in rows
                            ),
                        }
                        for threshold in args.thresholds
                    }
                detailed[name][case_type].append(case_result)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "persist_directory": settings.chroma_persist_directory,
        "embedding_model": settings.dashscope_embedding_model,
        "normal_case_count": len(normal_cases),
        "broad_case_count": len(broad_cases),
        "ood_case_count": len(ood_cases),
        "top_k": args.top_k,
        "thresholds": args.thresholds,
        "collections": {
            name: {
                "summary": _summarize_collection(
                    collections[name],
                    detailed[name]["normal"],
                    detailed[name]["broad"],
                    detailed[name]["ood"],
                    args.thresholds,
                ),
                "normal_results": detailed[name]["normal"],
                "broad_results": detailed[name]["broad"],
                "ood_results": detailed[name]["ood"],
            }
            for name in args.collections
        },
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {name: data["summary"] for name, data in report["collections"].items()},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
