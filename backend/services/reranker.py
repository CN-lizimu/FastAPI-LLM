from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

import dashscope


@dataclass(frozen=True)
class RerankScore:
    index: int
    score: float


def _call_dashscope_rerank(
    *,
    api_key: str,
    model: str,
    query: str,
    documents: Sequence[str],
    top_n: int,
) -> list[RerankScore]:
    response = dashscope.TextReRank.call(
        api_key=api_key,
        model=model,
        query=query,
        documents=list(documents),
        top_n=top_n,
        return_documents=False,
    )
    if getattr(response, "status_code", 500) != 200:
        message = getattr(response, "message", None) or "DashScope rerank request failed"
        raise RuntimeError(message)

    results = getattr(getattr(response, "output", None), "results", None) or []
    return [
        RerankScore(index=int(result.index), score=float(result.relevance_score))
        for result in results
    ]


async def rerank_documents(
    *,
    api_key: str,
    model: str,
    query: str,
    documents: Sequence[str],
    top_n: int,
    timeout_seconds: float,
) -> list[RerankScore]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _call_dashscope_rerank,
            api_key=api_key,
            model=model,
            query=query,
            documents=documents,
            top_n=top_n,
        ),
        timeout=timeout_seconds,
    )
