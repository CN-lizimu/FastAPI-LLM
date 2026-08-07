import json
import logging
from typing import Any

import redis.asyncio as redis

from config.settings import get_settings
from utils.observability import log_event


logger = logging.getLogger(__name__)
settings = get_settings()

redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=settings.redis_db,
    password=settings.redis_password or None,
    decode_responses=True,
    socket_timeout=settings.redis_socket_timeout_seconds,
    socket_connect_timeout=settings.redis_connect_timeout_seconds,
)


async def get_cache(key: str):
    try:
        return await redis_client.get(key)
    except redis.RedisError as exc:
        log_event(logger, logging.WARNING, "redis_get_failed", key=key, error_type=type(exc).__name__)
        return None


async def get_json_cache(key: str):
    try:
        data = await redis_client.get(key)
        return json.loads(data) if data else None
    except (redis.RedisError, json.JSONDecodeError) as exc:
        log_event(logger, logging.WARNING, "redis_json_get_failed", key=key, error_type=type(exc).__name__)
        return None


async def set_cache(key: str, value: Any, expire: int = 3600):
    try:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        await redis_client.setex(key, expire, value)
        return True
    except redis.RedisError as exc:
        log_event(logger, logging.WARNING, "redis_set_failed", key=key, error_type=type(exc).__name__)
        return False


async def delete_cache(*keys: str) -> int:
    if not keys:
        return 0
    try:
        return int(await redis_client.delete(*keys))
    except redis.RedisError as exc:
        log_event(
            logger,
            logging.WARNING,
            "redis_delete_failed",
            keys=list(keys),
            error_type=type(exc).__name__,
        )
        return 0


async def delete_cache_pattern(pattern: str) -> int:
    try:
        keys = [key async for key in redis_client.scan_iter(match=pattern, count=100)]
        return int(await redis_client.delete(*keys)) if keys else 0
    except redis.RedisError as exc:
        log_event(
            logger,
            logging.WARNING,
            "redis_pattern_delete_failed",
            pattern=pattern,
            error_type=type(exc).__name__,
        )
        return 0


async def close_redis() -> None:
    await redis_client.aclose()
