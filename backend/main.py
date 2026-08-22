import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config.cache_conf import close_redis
from config.db_conf import async_engine
from config.settings import get_settings
from routers import ai_chat, favorite, health, history, news, users
from services.db_bootstrap import ensure_ai_chat_tables, ensure_auth_tables
from utils.exception_handlers import register_exception_handlers
from utils.observability import configure_logging, log_event, reset_trace_id, set_trace_id


settings = get_settings()
configure_logging(settings.app_log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="FastAPI LLM News API", version="1.0.0")


def _is_sse_response(response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return content_type.startswith("text/event-stream")


@app.middleware("http")
async def trace_request(request: Request, call_next):
    incoming_trace_id = request.headers.get("X-Trace-Id", "").strip()
    trace_id = incoming_trace_id[:64] if incoming_trace_id else str(uuid.uuid4())
    request.state.trace_id = trace_id
    token = set_trace_id(trace_id)
    started = time.perf_counter()
    status_code = 500
    streaming = False
    try:
        response = await call_next(request)
        status_code = response.status_code
        streaming = _is_sse_response(response)
        response.headers["X-Trace-Id"] = trace_id
        return response
    finally:
        log_event(
            logger,
            logging.INFO,
            "http_stream_started" if streaming else "http_request_completed",
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            streaming=streaming,
            timing_scope="response_headers_ready" if streaming else "response_completed",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        reset_trace_id(token)


@app.on_event("startup")
async def startup_db_bootstrap():
    log_event(
        logger,
        logging.INFO,
        "model_configuration_loaded",
        chat_model=settings.chat_model,
        embedding_model=settings.embedding_model,
        collection=settings.rag_collection_name,
    )
    if not settings.chat_model_is_allowed:
        log_event(
            logger,
            logging.WARNING,
            "model_policy_warning",
            model_type="chat",
            configured_model=settings.chat_model,
            allowed_models=settings.allowed_chat_model_list,
            reason="CHAT_MODEL is not in ALLOWED_CHAT_MODELS",
        )
    if not settings.embedding_model_is_allowed:
        log_event(
            logger,
            logging.WARNING,
            "model_policy_warning",
            model_type="embedding",
            configured_model=settings.embedding_model,
            allowed_models=settings.allowed_embedding_model_list,
            reason="EMBEDDING_MODEL is not in ALLOWED_EMBEDDING_MODELS",
        )
    await ensure_auth_tables()
    await ensure_ai_chat_tables()
    from services.retriever_factory import get_news_vector_store

    get_news_vector_store()


@app.on_event("shutdown")
async def shutdown_resources():
    await close_redis()
    await async_engine.dispose()


register_exception_handlers(app)

cors_origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=settings.cors_allow_credentials and "*" not in cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
    expose_headers=["X-Session-Id", "X-Trace-Id"],
)


@app.get("/")
async def root():
    return {"message": "Hello World"}


app.include_router(health.router)
app.include_router(news.router)
app.include_router(users.router)
app.include_router(favorite.router)
app.include_router(history.router)
app.include_router(ai_chat.router)
