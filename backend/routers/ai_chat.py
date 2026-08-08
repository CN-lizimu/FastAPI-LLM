import asyncio
import json
import logging
import time
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from sqlalchemy.ext.asyncio import AsyncSession

from config.db_conf import AsyncSessionLocal, get_db
from config.settings import get_settings
from crud import ai_chat as ai_chat_crud
from models.users import User
from schemas.ai_chat import AIChatRequest, ChatMessage, ChatSessionMessagesResponse, ChatSessionResponse
from services.ai_runtime import AIConcurrencyTimeout, acquire_llm_slot, release_llm_slot
from services.get_retrievel import get_retrievel_chain, needs_query_rewrite
from services.model_factory import (
    get_chat_model,
    get_chat_model_name,
    get_dashscope_api_key,
    get_dashscope_chat_endpoint,
)
from services.retriever_factory import RetrievalResult, retrieve_news
from services.update_summary import refresh_session_summary_if_needed
from utils.auth import get_current_user
from utils.create_prompt import (
    SYSTEM_PROMPT_TEMPLATE_PATH,
    USER_PROMPT_TEMPLATE_PATH,
    build_langchain_summary_history,
    extract_latest_user_query,
    load_template_text,
)
from utils.observability import log_event, reset_trace_id, set_trace_id, truncate_log_text
from utils.response import success_response


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])
NO_RELEVANT_DOCUMENTS_ANSWER = "根据当前新闻数据无法确定"


@router.get("/sessions")
async def get_chat_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = await ai_chat_crud.get_user_chat_sessions(db, user.id, limit=100)
    data = [
        ChatSessionResponse(session_id=session.session_id, title=session.title, summary=session.summary)
        for session in sessions
    ]
    return success_response(message="获取用户会话记录成功", data=data)


@router.get("/sessions/{session_id}/messages")
async def get_chat_session_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await ai_chat_crud.get_chat_session(db, user.id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或不属于当前用户")

    messages = await ai_chat_crud.get_chat_content_by_session(db, user.id, session_id)
    response_data = ChatSessionMessagesResponse(
        session_id=session_id,
        messages=[ChatMessage(role=message.role, content=message.content) for message in messages],
    )
    return success_response(data=response_data)


def _format_rag_docs(docs) -> str:
    if not docs:
        return ""
    return "\n\n".join(
        "\n".join(
            [
                f"【新闻ID】{doc.metadata.get('news_id', '')}",
                f"【标题】{doc.metadata.get('title', '未知标题')}",
                f"【发布时间】{doc.metadata.get('publish_time', '')}",
                f"【来源】{doc.metadata.get('source', 'mysql.news')}",
                f"【相关度】{doc.metadata.get('relevance_score', 0):.4f}",
                f"【内容】{doc.page_content}",
            ]
        )
        for doc in docs
    )


def _build_sources(docs) -> list[dict]:
    sources = []
    for doc in docs:
        metadata = doc.metadata
        sources.append(
            {
                "news_id": metadata.get("news_id"),
                "title": metadata.get("title"),
                "category_id": metadata.get("category_id"),
                "publish_time": metadata.get("publish_time"),
                "source": metadata.get("source"),
                "score": round(float(metadata.get("relevance_score", 0)), 4),
                "dense_rank": metadata.get("dense_rank"),
                "bm25_rank": metadata.get("bm25_rank"),
                "rrf_score": metadata.get("rrf_score"),
                "rerank_score": metadata.get("rerank_score"),
                "final_rank": metadata.get("final_rank"),
            }
        )
    return sources


def _sse_payload(data) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _content_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, ensure_ascii=False, default=str))


def _prompt_observability(generation_prompt, generation_input: dict, docs) -> dict:
    formatted_messages = generation_prompt.format_messages(**generation_input)
    document_keys = [
        (doc.metadata.get("news_id"), doc.metadata.get("chunk_index")) for doc in docs
    ]
    return {
        "input_context_chars": sum(
            _content_chars(message.content) for message in formatted_messages
        ),
        "input_message_count": len(formatted_messages),
        "rag_context_chars": len(generation_input["RAG_results"]),
        "rag_document_chars": sum(len(doc.page_content) for doc in docs),
        "history_context_chars": sum(
            _content_chars(message.content) for message in generation_input["recent_messages"]
        ),
        "duplicate_document_count": len(document_keys) - len(set(document_keys)),
    }


def _extract_token_usage(message) -> dict[str, int] | None:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        response_metadata = getattr(message, "response_metadata", None) or {}
        usage = response_metadata.get("token_usage") or response_metadata.get("usage")
    if not isinstance(usage, dict):
        return None

    values = {
        "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "total_tokens": usage.get("total_tokens"),
    }
    if not any(isinstance(value, int) for value in values.values()):
        return None
    return {key: value for key, value in values.items() if isinstance(value, int)}


def _reasoning_chars(message) -> int:
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    reasoning_content = additional_kwargs.get("reasoning_content")
    return len(reasoning_content) if isinstance(reasoning_content, str) else 0


def _usage_log_fields(usage: dict[str, int] | None) -> dict[str, int | None]:
    usage = usage or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


async def _prepare_rag(question: str, recent_messages: list, user_id: int, session_id: str):
    settings = get_settings()
    rewrite_started = time.perf_counter()
    rewrite_required = needs_query_rewrite(question, recent_messages)
    if rewrite_required:
        rewritten_query = await asyncio.wait_for(
            get_retrievel_chain().ainvoke(
                {"query": question, "recent_messages": recent_messages}
            ),
            timeout=settings.llm_request_timeout_seconds,
        )
        rewritten_query = rewritten_query.strip() or question
        rewrite_reason = "context_reference_detected"
    else:
        rewritten_query = question
        rewrite_reason = "independent_query"
    rewrite_ms = round((time.perf_counter() - rewrite_started) * 1000, 2)
    log_event(
        logger,
        logging.INFO,
        "rag_query_rewrite_completed",
        user_id=user_id,
        session_id=session_id,
        original_query=question,
        rewritten_query=rewritten_query,
        rewrite_required=rewrite_required,
        rewrite_applied=rewritten_query.strip() != question.strip(),
        reason=rewrite_reason,
        duration_ms=rewrite_ms,
    )

    retrieval_result: RetrievalResult = await asyncio.wait_for(
        retrieve_news(rewritten_query),
        timeout=settings.rag_retrieval_timeout_seconds,
    )
    return rewritten_query, retrieval_result, rewrite_ms


@router.post("/chat")
async def ai_chat(
    payload: AIChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    trace_id = request.state.trace_id
    request_started = time.perf_counter()
    slot_acquired = False
    stream_owns_slot = False

    if payload.session_id:
        current_session = await ai_chat_crud.get_chat_session(db, user.id, payload.session_id)
        if not current_session:
            raise HTTPException(status_code=404, detail="会话不存在或不属于当前用户")
        session_id = payload.session_id
    else:
        current_session = await ai_chat_crud.create_chat_session(db, user.id)
        session_id = current_session.session_id

    try:
        current_question = extract_latest_user_query(payload.messages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_event(
        logger,
        logging.INFO,
        "ai_chat_request_received",
        user_id=user.id,
        session_id=session_id,
        query=current_question,
        query_length=len(current_question),
        stream=payload.stream,
    )

    api_key = get_dashscope_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="AI 服务尚未配置")

    try:
        await acquire_llm_slot()
        slot_acquired = True
    except AIConcurrencyTimeout as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        summary = await ai_chat_crud.get_chat_session_summary(db, user.id, session_id)
        recent_context = await ai_chat_crud.get_recent_chat_messages(
            db,
            user.id,
            session_id,
            limit=settings.chat_history_limit,
        )
        recent_messages = build_langchain_summary_history(summary, recent_context)

        try:
            rewritten_query, retrieval_result, rewrite_ms = await _prepare_rag(
                current_question,
                recent_messages,
                user.id,
                session_id,
            )
            docs = retrieval_result.documents
            retrieval_ms = retrieval_result.timings_ms["retrieval_total_ms"]
        except TimeoutError as exc:
            log_event(
                logger,
                logging.ERROR,
                "rag_prepare_timeout",
                user_id=user.id,
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            raise HTTPException(status_code=504, detail="AI 检索准备超时，请稍后重试") from exc
        except Exception as exc:
            logger.exception("RAG preparation failed")
            log_event(
                logger,
                logging.ERROR,
                "rag_prepare_failed",
                user_id=user.id,
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            raise HTTPException(status_code=502, detail="AI 检索服务暂时不可用") from exc

        system_prompt_text = load_template_text(SYSTEM_PROMPT_TEMPLATE_PATH)
        user_prompt_text = load_template_text(USER_PROMPT_TEMPLATE_PATH)
        generation_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt_text),
                MessagesPlaceholder(variable_name="recent_messages"),
                ("human", user_prompt_text if user_prompt_text.strip() else "{query}"),
            ]
        )
        generation_chain = generation_prompt | get_chat_model()
        generation_input = {
            "query": current_question,
            "recent_messages": recent_messages,
            "RAG_results": _format_rag_docs(docs),
        }
        prompt_metrics = _prompt_observability(generation_prompt, generation_input, docs)
        sources = _build_sources(docs)
        active_model_name = get_chat_model_name()

        if settings.rag_debug_log:
            log_event(
                logger,
                logging.INFO,
                "rag_debug_context",
                user_id=user.id,
                session_id=session_id,
                original_query=current_question,
                rewritten_query=rewritten_query,
                rag_results=truncate_log_text(
                    generation_input["RAG_results"],
                    settings.rag_debug_max_chars,
                ),
                prompt_summary={
                    "system": truncate_log_text(system_prompt_text, settings.rag_debug_max_chars),
                    "user_template": truncate_log_text(user_prompt_text, settings.rag_debug_max_chars),
                    "history_message_count": len(recent_messages),
                },
            )

        await ai_chat_crud.add_chat_message(
            db=db,
            user_id=user.id,
            session_id=session_id,
            role="user",
            content=current_question,
            model_name=active_model_name,
        )

        if payload.stream:
            last_summary_index = current_session.last_summary_index
            api_endpoint = get_dashscope_chat_endpoint()
            await db.close()
            stream_owns_slot = True

            async def stream_generator():
                trace_token = set_trace_id(trace_id)
                assistant_text_parts: list[str] = []
                llm_started = time.perf_counter()
                completed = False
                generation_succeeded = False
                generation_finished_at = None
                chunk_count = 0
                first_token_ms = None
                token_usage = None
                reasoning_character_count = 0
                model_stream = generation_chain.astream(generation_input) if docs else None
                log_event(
                    logger,
                    logging.INFO,
                    "llm_generation_started",
                    user_id=user.id,
                    session_id=session_id,
                    model=active_model_name,
                    temperature=settings.llm_temperature,
                    max_tokens=settings.llm_max_tokens,
                    stream=True,
                    contextual_document_count=len(docs),
                    skipped_due_to_no_context=not docs,
                    **prompt_metrics,
                )
                try:
                    if sources:
                        yield _sse_payload({"sources": sources})

                    async with asyncio.timeout(settings.llm_stream_timeout_seconds):
                        if model_stream is None:
                            chunk_count = 1
                            assistant_text_parts.append(NO_RELEVANT_DOCUMENTS_ANSWER)
                            yield _sse_payload(
                                {"choices": [{"delta": {"content": NO_RELEVANT_DOCUMENTS_ANSWER}}]}
                            )
                        else:
                            async for chunk in model_stream:
                                if await request.is_disconnected():
                                    log_event(
                                        logger,
                                        logging.INFO,
                                        "sse_client_disconnected",
                                        user_id=user.id,
                                        session_id=session_id,
                                    )
                                    raise asyncio.CancelledError

                                chunk_usage = _extract_token_usage(chunk)
                                if chunk_usage:
                                    token_usage = chunk_usage
                                reasoning_character_count += _reasoning_chars(chunk)
                                content = getattr(chunk, "content", "") or ""
                                if not content:
                                    continue
                                if first_token_ms is None:
                                    first_token_ms = round(
                                        (time.perf_counter() - llm_started) * 1000, 2
                                    )
                                chunk_count += 1
                                assistant_text_parts.append(content)
                                yield _sse_payload({"choices": [{"delta": {"content": content}}]})

                    generation_finished_at = time.perf_counter()
                    generation_succeeded = True
                    assistant_text = "".join(assistant_text_parts).strip()
                    if assistant_text:
                        async with AsyncSessionLocal() as stream_db:
                            new_message = await ai_chat_crud.add_chat_message(
                                db=stream_db,
                                user_id=user.id,
                                session_id=session_id,
                                role="assistant",
                                content=assistant_text,
                                model_name=active_model_name,
                                finish_reason="stop",
                            )
                            await refresh_session_summary_if_needed(
                                stream_db,
                                user.id,
                                session_id,
                                last_summary_index,
                                new_message.message_index,
                                api_endpoint,
                                api_key,
                                active_model_name,
                            )
                    completed = True
                    yield b"data: [DONE]\n\n"
                except TimeoutError:
                    log_event(
                        logger,
                        logging.ERROR,
                        "llm_stream_timeout",
                        user_id=user.id,
                        session_id=session_id,
                    )
                    yield _sse_payload(
                        {
                            "code": 504,
                            "message": "AI 生成超时，请稍后重试",
                            "data": None,
                            "trace_id": trace_id,
                        }
                    )
                    yield b"data: [DONE]\n\n"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("LLM streaming failed")
                    log_event(
                        logger,
                        logging.ERROR,
                        "llm_stream_failed",
                        user_id=user.id,
                        session_id=session_id,
                        error_type=type(exc).__name__,
                    )
                    yield _sse_payload(
                        {
                            "code": 502,
                            "message": "上游模型服务暂时不可用",
                            "data": None,
                            "trace_id": trace_id,
                        }
                    )
                    yield b"data: [DONE]\n\n"
                finally:
                    if model_stream is not None:
                        with suppress(Exception):
                            await model_stream.aclose()
                    release_llm_slot()
                    finished_at = generation_finished_at or time.perf_counter()
                    llm_ms = round((finished_at - llm_started) * 1000, 2)
                    post_generation_ms = round(
                        max(0.0, time.perf_counter() - finished_at) * 1000,
                        2,
                    )
                    log_event(
                        logger,
                        logging.INFO,
                        "llm_generation_completed",
                        user_id=user.id,
                        session_id=session_id,
                        generation_duration_ms=llm_ms,
                        generation_total_ms=llm_ms,
                        time_to_first_token_ms=first_token_ms,
                        input_context_chars=prompt_metrics["input_context_chars"],
                        output_character_count=sum(len(part) for part in assistant_text_parts),
                        output_chars=sum(len(part) for part in assistant_text_parts),
                        reasoning_character_count=reasoning_character_count,
                        reasoning_detected=reasoning_character_count > 0,
                        success=generation_succeeded,
                        sse_chunk_count=chunk_count,
                        skipped_due_to_no_context=not docs,
                        **_usage_log_fields(token_usage),
                    )
                    log_event(
                        logger,
                        logging.INFO,
                        "ai_chat_completed",
                        user_id=user.id,
                        session_id=session_id,
                        success=completed,
                        rewrite_ms=rewrite_ms,
                        retrieval_ms=retrieval_ms,
                        **retrieval_result.timings_ms,
                        llm_ms=llm_ms,
                        post_generation_ms=post_generation_ms,
                        total_ms=round((time.perf_counter() - request_started) * 1000, 2),
                        retrieved_document_count=len(docs),
                    )
                    reset_trace_id(trace_token)

            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream",
                headers={"X-Session-Id": session_id, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        llm_started = time.perf_counter()
        llm_succeeded = False
        assistant_text = ""
        token_usage = None
        reasoning_character_count = 0
        llm_ms = 0.0
        log_event(
            logger,
            logging.INFO,
            "llm_generation_started",
            user_id=user.id,
            session_id=session_id,
            model=active_model_name,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            stream=False,
            contextual_document_count=len(docs),
            skipped_due_to_no_context=not docs,
            **prompt_metrics,
        )
        try:
            if docs:
                ai_message = await asyncio.wait_for(
                    generation_chain.ainvoke(generation_input),
                    timeout=settings.llm_request_timeout_seconds,
                )
                assistant_text = getattr(ai_message, "content", "") or ""
                token_usage = _extract_token_usage(ai_message)
                reasoning_character_count = _reasoning_chars(ai_message)
            else:
                assistant_text = NO_RELEVANT_DOCUMENTS_ANSWER
            llm_succeeded = True
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="AI 生成超时，请稍后重试") from exc
        except Exception as exc:
            logger.exception("LLM invocation failed")
            raise HTTPException(status_code=502, detail="上游模型服务暂时不可用") from exc
        finally:
            llm_ms = round((time.perf_counter() - llm_started) * 1000, 2)
            log_event(
                logger,
                logging.INFO,
                "llm_generation_completed",
                user_id=user.id,
                session_id=session_id,
                generation_duration_ms=llm_ms,
                generation_total_ms=llm_ms,
                time_to_first_token_ms=None,
                input_context_chars=prompt_metrics["input_context_chars"],
                output_character_count=len(assistant_text),
                output_chars=len(assistant_text),
                reasoning_character_count=reasoning_character_count,
                reasoning_detected=reasoning_character_count > 0,
                success=llm_succeeded,
                sse_chunk_count=0,
                skipped_due_to_no_context=not docs,
                **_usage_log_fields(token_usage),
            )

        post_generation_started = time.perf_counter()
        if assistant_text:
            new_message = await ai_chat_crud.add_chat_message(
                db=db,
                user_id=user.id,
                session_id=session_id,
                role="assistant",
                content=assistant_text,
                model_name=active_model_name,
                finish_reason="stop",
            )
            await refresh_session_summary_if_needed(
                db,
                user.id,
                session_id,
                current_session.last_summary_index,
                new_message.message_index,
                get_dashscope_chat_endpoint(),
                api_key,
                active_model_name,
            )

        post_generation_ms = round(
            (time.perf_counter() - post_generation_started) * 1000,
            2,
        )
        log_event(
            logger,
            logging.INFO,
            "ai_chat_completed",
            user_id=user.id,
            session_id=session_id,
            success=True,
            rewrite_ms=rewrite_ms,
            retrieval_ms=retrieval_ms,
            **retrieval_result.timings_ms,
            llm_ms=llm_ms,
            post_generation_ms=post_generation_ms,
            total_ms=round((time.perf_counter() - request_started) * 1000, 2),
            retrieved_document_count=len(docs),
        )
        return success_response(
            message="聊天成功",
            data={
                "session_id": session_id,
                "result": {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": assistant_text},
                            "finish_reason": "stop",
                        }
                    ]
                },
                "sources": sources,
            },
        )
    finally:
        if slot_acquired and not stream_owns_slot:
            release_llm_slot()
