import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette import status

from utils.observability import get_trace_id, log_event


logger = logging.getLogger(__name__)


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", get_trace_id())


def _error_response(status_code: int, message: str, request: Request, data=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": message,
            "data": data,
            "trace_id": _trace_id(request),
        },
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    log_event(
        logger,
        logging.WARNING,
        "http_exception",
        trace_id=_trace_id(request),
        path=request.url.path,
        status_code=exc.status_code,
        error_type=type(exc).__name__,
    )
    return _error_response(exc.status_code, str(exc.detail), request)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    fields = [
        {"location": list(error.get("loc", ())), "message": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    log_event(
        logger,
        logging.WARNING,
        "request_validation_failed",
        trace_id=_trace_id(request),
        path=request.url.path,
        error_count=len(fields),
    )
    return _error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, "请求参数校验失败", request, {"fields": fields})


async def integrity_error_handler(request: Request, exc: IntegrityError):
    error_msg = str(exc.orig)
    if "username_UNIQUE" in error_msg or "Duplicate entry" in error_msg:
        detail = "数据已存在，请勿重复提交"
    elif "FOREIGN KEY" in error_msg:
        detail = "关联数据不存在"
    else:
        detail = "数据约束冲突，请检查输入"

    log_event(
        logger,
        logging.ERROR,
        "database_integrity_error",
        trace_id=_trace_id(request),
        path=request.url.path,
        error_type=type(exc).__name__,
        exc_info=True,
    )
    logger.error(
        "database integrity constraint failed",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _error_response(status.HTTP_400_BAD_REQUEST, detail, request)


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    log_event(
        logger,
        logging.ERROR,
        "database_error",
        trace_id=_trace_id(request),
        path=request.url.path,
        error_type=type(exc).__name__,
        exc_info=True,
    )
    logger.error("database operation failed", exc_info=(type(exc), exc, exc.__traceback__))
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "数据库操作失败，请稍后重试", request)


async def general_exception_handler(request: Request, exc: Exception):
    log_event(
        logger,
        logging.ERROR,
        "unhandled_exception",
        trace_id=_trace_id(request),
        path=request.url.path,
        error_type=type(exc).__name__,
    )
    logger.error("unhandled application exception", exc_info=(type(exc), exc, exc.__traceback__))
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "服务器内部错误", request)
