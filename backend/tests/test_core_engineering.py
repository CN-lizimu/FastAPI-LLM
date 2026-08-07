from datetime import timedelta

import pytest
from fastapi import Request

from config.chroma_conf import load_config_from_env
from config.settings import get_settings
from routers.ai_chat import _build_sources, _format_rag_docs
from utils.exception import general_exception_handler
from utils.jwt_tokens import create_jwt, decode_jwt
from utils.security import get_hash_password, verify_password


def test_central_settings_and_rag_config_are_consistent():
    settings = get_settings()
    rag = load_config_from_env()

    assert settings.database_url
    assert settings.cors_origin_list
    assert "*" not in settings.cors_origin_list
    assert rag.collection_name == settings.chroma_collection_name
    assert rag.persist_directory == settings.chroma_persist_directory
    assert rag.chunk_size == settings.rag_chunk_size
    assert rag.chunk_overlap == settings.rag_chunk_overlap
    assert rag.chunk_overlap < rag.chunk_size


def test_jwt_round_trip_uses_claims_and_expiry():
    token, claims = create_jwt(
        user_id=123,
        username="interview-user",
        token_type="access",
        token_version=1,
        expires_delta=timedelta(minutes=5),
    )

    decoded = decode_jwt(token, expected_type="access")
    assert decoded["sub"] == "123"
    assert decoded["jti"] == claims["jti"]
    assert decoded["exp"] > decoded["iat"]


def test_password_hash_round_trip_and_wrong_password():
    hashed = get_hash_password("Correct-Horse-123")
    assert hashed != "Correct-Horse-123"
    assert verify_password("Correct-Horse-123", hashed)
    assert not verify_password("wrong-password", hashed)


@pytest.mark.asyncio
async def test_general_exception_response_does_not_leak_stack():
    request = Request({"type": "http", "method": "GET", "path": "/boom", "headers": []})
    request.state.trace_id = "test-trace"
    response = await general_exception_handler(request, RuntimeError("private database detail"))

    assert response.status_code == 500
    assert b"private database detail" not in response.body
    assert b"traceback" not in response.body.lower()
    assert b"test-trace" in response.body


def test_rag_context_and_sources_keep_provenance():
    from langchain_core.documents import Document

    docs = [
        Document(
            page_content="新闻正文",
            metadata={
                "news_id": 8,
                "title": "量子计算突破",
                "category_id": 3,
                "publish_time": "2024-01-01",
                "source": "mysql.news",
                "relevance_score": 0.87654,
            },
        )
    ]

    context = _format_rag_docs(docs)
    sources = _build_sources(docs)
    assert "量子计算突破" in context
    assert "新闻ID】8" in context
    assert sources[0]["news_id"] == 8
    assert sources[0]["score"] == 0.8765
