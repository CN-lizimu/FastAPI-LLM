from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from config.settings import get_settings
from services.model_factory import get_chat_model
from services.retriever_factory import retrieve_news_documents
from utils.create_prompt import load_system_prompt_text


BASE_DIR = Path(__file__).resolve().parent


def _load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_docs(docs) -> str:
    return "\n\n".join(
        f"【新闻ID】{doc.metadata.get('news_id')}\n"
        f"【标题】{doc.metadata.get('title')}\n"
        f"【内容】{doc.page_content}"
        for doc in docs
    )


async def _generate_answer(question: str, docs) -> str:
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", load_system_prompt_text()),
            ("human", "用户问：\n{query}"),
        ]
    )
    response = await asyncio.wait_for(
        (prompt | get_chat_model()).ainvoke(
            {"query": question, "RAG_results": _format_docs(docs)}
        ),
        timeout=get_settings().llm_request_timeout_seconds,
    )
    return getattr(response, "content", "") or ""


async def evaluate_case(case: dict, generate_answer: bool) -> dict:
    total_started = time.perf_counter()
    retrieval_started = time.perf_counter()
    docs = await asyncio.wait_for(
        retrieve_news_documents(case["question"]),
        timeout=get_settings().rag_retrieval_timeout_seconds,
    )
    retrieval_latency_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)

    retrieved_documents = [
        {
            "news_id": doc.metadata.get("news_id"),
            "title": doc.metadata.get("title"),
            "score": round(float(doc.metadata.get("relevance_score", 0)), 4),
        }
        for doc in docs
    ]
    retrieved_ids = {item["news_id"] for item in retrieved_documents}
    expected_document_id = case.get("expected_document_id")
    answer = await _generate_answer(case["question"], docs) if generate_answer else None

    return {
        **case,
        "retrieved_documents": retrieved_documents,
        "retrieval_latency_ms": retrieval_latency_ms,
        "answer": answer,
        "total_latency_ms": round((time.perf_counter() - total_started) * 1000, 2),
        "recall_at_k": int(expected_document_id in retrieved_ids) if expected_document_id is not None else None,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the minimal news RAG evaluation set.")
    parser.add_argument("--cases", type=Path, default=BASE_DIR / "rag_cases.json")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "rag_results.json")
    parser.add_argument("--generate-answers", action="store_true")
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    results = []
    for case in cases:
        results.append(await evaluate_case(case, args.generate_answers))

    scored = [item["recall_at_k"] for item in results if item["recall_at_k"] is not None]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "top_k": get_settings().rag_top_k,
            "score_threshold": get_settings().rag_score_threshold,
            "embedding_model": get_settings().dashscope_embedding_model,
        },
        "summary": {
            "case_count": len(results),
            "recall_at_k": round(sum(scored) / len(scored), 4) if scored else None,
            "answers_generated": args.generate_answers,
        },
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
