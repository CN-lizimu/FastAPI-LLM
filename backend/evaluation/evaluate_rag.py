from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import get_settings
from services.retriever_factory import RetrievalOptions, retrieve_news


BASE_DIR = Path(__file__).resolve().parent
PIPELINES = {
    "dense": RetrievalOptions(mode="dense", rerank_enabled=False),
    "hybrid": RetrievalOptions(mode="hybrid", rerank_enabled=False),
    "hybrid_rerank": RetrievalOptions(mode="hybrid", rerank_enabled=True),
}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 2)


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "mean": None, "median": None, "max": None}
    return {
        "min": round(min(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "max": round(max(values), 4),
    }


def _serialize_document(document) -> dict[str, Any]:
    metadata = document.metadata
    return {
        "news_id": metadata.get("news_id"),
        "title": metadata.get("title"),
        "chunk_index": metadata.get("chunk_index"),
        "dense_score": metadata.get("dense_score"),
        "dense_rank": metadata.get("dense_rank"),
        "bm25_score": metadata.get("bm25_score"),
        "bm25_rank": metadata.get("bm25_rank"),
        "rrf_score": metadata.get("rrf_score"),
        "pre_rerank_rank": metadata.get("pre_rerank_rank"),
        "rerank_score": metadata.get("rerank_score"),
        "final_rank": metadata.get("final_rank"),
    }


def _documents_at_threshold(result: dict[str, Any], threshold: float) -> list[dict[str, Any]]:
    if result["highest_dense_score"] is None or result["highest_dense_score"] < threshold:
        return []
    if result["pipeline"] == "dense":
        return [
            document
            for document in result["retrieved_documents"]
            if (document["dense_score"] or float("-inf")) >= threshold
        ]
    return result["retrieved_documents"]


def _rank_of_expected(documents: list[dict[str, Any]], expected_document_id: int) -> int | None:
    for rank, document in enumerate(documents, start=1):
        if document["news_id"] == expected_document_id:
            return rank
    return None


async def _evaluate_query(
    *,
    case: dict[str, Any],
    pipeline: str,
    options: RetrievalOptions,
    base_threshold: float,
) -> dict[str, Any]:
    settings = get_settings()
    effective_options = RetrievalOptions(
        mode=options.mode,
        rerank_enabled=options.rerank_enabled,
        dense_candidate_k=settings.rag_dense_candidate_k,
        bm25_candidate_k=settings.rag_bm25_candidate_k,
        rerank_candidate_k=settings.rag_rerank_candidate_k,
        final_top_k=settings.rag_final_top_k,
        score_threshold=base_threshold,
    )
    timeout = settings.rag_retrieval_timeout_seconds
    if options.rerank_enabled:
        timeout += settings.rag_rerank_timeout_seconds
    retrieval = await asyncio.wait_for(
        retrieve_news(case["question"], effective_options), timeout=timeout
    )
    return {
        **case,
        "pipeline": pipeline,
        "highest_dense_score": retrieval.highest_dense_score,
        "retrieved_documents": [_serialize_document(doc) for doc in retrieval.documents],
        "retrieval_latency_ms": retrieval.timings_ms["retrieval_total_ms"],
        "timings_ms": retrieval.timings_ms,
        "candidate_counts": retrieval.candidate_counts,
        "rerank_succeeded": retrieval.rerank_succeeded,
    }


def _normal_metrics(results: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    ranks = [
        _rank_of_expected(
            _documents_at_threshold(result, threshold), result["expected_document_id"]
        )
        for result in results
    ]
    metrics: dict[str, Any] = {}
    for k in (1, 3, 5):
        hits = [int(rank is not None and rank <= k) for rank in ranks]
        # Every current case has one relevant news ID, so Hit@K and Recall@K are equal.
        metrics[f"recall_at_{k}"] = round(statistics.fmean(hits), 4)
        metrics[f"hit_at_{k}"] = round(statistics.fmean(hits), 4)
    metrics["mrr"] = round(
        statistics.fmean(1.0 / rank if rank is not None else 0.0 for rank in ranks), 4
    )
    metrics["queries_without_context"] = sum(
        not _documents_at_threshold(result, threshold) for result in results
    )
    return metrics


def _ood_metrics(results: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    details = []
    for result in results:
        accepted_documents = _documents_at_threshold(result, threshold)
        details.append(
            {
                "id": result["id"],
                "question": result["question"],
                "highest_score": result["highest_dense_score"],
                "threshold": threshold,
                "accepted_document_count": len(accepted_documents),
                "correctly_rejected": not accepted_documents,
            }
        )
    return {
        "ood_rejection_accuracy": round(
            statistics.fmean(int(item["correctly_rejected"]) for item in details), 4
        ),
        "details": details,
    }


def _pipeline_summary(
    normal_results: list[dict[str, Any]],
    ood_results: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    latencies = [result["retrieval_latency_ms"] for result in normal_results]
    normal_scores = [
        result["highest_dense_score"]
        for result in normal_results
        if result["highest_dense_score"] is not None
    ]
    ood_scores = [
        result["highest_dense_score"]
        for result in ood_results
        if result["highest_dense_score"] is not None
    ]
    return {
        **_normal_metrics(normal_results, threshold),
        "ood_rejection_accuracy": _ood_metrics(ood_results, threshold)[
            "ood_rejection_accuracy"
        ],
        "average_latency_ms": round(statistics.fmean(latencies), 2),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "normal_highest_score_distribution": _distribution(normal_scores),
        "ood_highest_score_distribution": _distribution(ood_scores),
        "rerank_failures": sum(
            result["pipeline"] == "hybrid_rerank" and not result["rerank_succeeded"]
            for result in normal_results + ood_results
        ),
    }


async def run_evaluation(
    *,
    normal_cases: list[dict[str, Any]],
    ood_cases: list[dict[str, Any]],
    thresholds: list[float],
) -> dict[str, Any]:
    base_threshold = min(thresholds)
    warmup_case = normal_cases[0]
    for pipeline, options in PIPELINES.items():
        print(f"[{pipeline}] warm-up (excluded from latency metrics)")
        await _evaluate_query(
            case=warmup_case,
            pipeline=pipeline,
            options=options,
            base_threshold=base_threshold,
        )

    details: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for pipeline, options in PIPELINES.items():
        details[pipeline] = {"normal": [], "ood": []}
        for case_type, cases in (("normal", normal_cases), ("ood", ood_cases)):
            for index, case in enumerate(cases, start=1):
                print(f"[{pipeline}] {case_type} {index}/{len(cases)}: {case['id']}")
                details[pipeline][case_type].append(
                    await _evaluate_query(
                        case=case,
                        pipeline=pipeline,
                        options=options,
                        base_threshold=base_threshold,
                    )
                )

    settings = get_settings()
    summaries = {
        pipeline: _pipeline_summary(
            pipeline_details["normal"],
            pipeline_details["ood"],
            settings.rag_score_threshold,
        )
        for pipeline, pipeline_details in details.items()
    }
    threshold_experiments = {
        str(threshold): {
            pipeline: {
                **_normal_metrics(pipeline_details["normal"], threshold),
                "ood_rejection_accuracy": _ood_metrics(
                    pipeline_details["ood"], threshold
                )["ood_rejection_accuracy"],
            }
            for pipeline, pipeline_details in details.items()
        }
        for threshold in thresholds
    }
    ood_details = {
        pipeline: _ood_metrics(pipeline_details["ood"], settings.rag_score_threshold)[
            "details"
        ]
        for pipeline, pipeline_details in details.items()
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
        "collection": settings.rag_collection_name,
            "distance_metric": settings.chroma_distance_metric,
        "embedding_model": settings.embedding_model,
            "dense_candidate_k": settings.rag_dense_candidate_k,
            "bm25_candidate_k": settings.rag_bm25_candidate_k,
            "rrf_k": settings.rag_rrf_k,
            "rerank_candidate_k": settings.rag_rerank_candidate_k,
            "rerank_model": settings.rag_rerank_model,
            "final_top_k": settings.rag_final_top_k,
            "default_threshold": settings.rag_score_threshold,
            "threshold_stage": "highest dense cosine relevance score query gate",
        },
        "normal_case_count": len(normal_cases),
        "ood_case_count": len(ood_cases),
        "warmup_excluded_from_metrics": True,
        "summaries": summaries,
        "threshold_experiments": threshold_experiments,
        "ood_details": ood_details,
        "details": details,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Dense, Hybrid, and Hybrid + Rerank RAG.")
    parser.add_argument("--cases", type=Path, default=BASE_DIR / "rag_cases.json")
    parser.add_argument("--ood-cases", type=Path, default=BASE_DIR / "rag_ood_cases.json")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "rag_v2_results.json")
    parser.add_argument(
        "--thresholds", nargs="+", type=float, default=[0.3, 0.4, 0.5, 0.6]
    )
    args = parser.parse_args()
    report = await run_evaluation(
        normal_cases=_load_cases(args.cases),
        ood_cases=_load_cases(args.ood_cases),
        thresholds=args.thresholds,
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report["summaries"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
