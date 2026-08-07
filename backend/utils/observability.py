from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any


_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def configure_logging(level_name: str = "INFO") -> None:
    """Configure the root logger even when Uvicorn installed handlers first."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(handler)


def truncate_log_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"


def get_trace_id() -> str:
    return _trace_id.get()


def set_trace_id(value: str) -> Token:
    return _trace_id.set(value)


def reset_trace_id(token: Token) -> None:
    _trace_id.reset(token)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
        "trace_id": get_trace_id(),
        **fields,
    }
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))
