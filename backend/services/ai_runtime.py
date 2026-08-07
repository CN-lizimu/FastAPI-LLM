import asyncio
from functools import lru_cache

from config.settings import get_settings


class AIConcurrencyTimeout(TimeoutError):
    pass


@lru_cache(maxsize=1)
def get_llm_semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().llm_max_concurrency)


async def acquire_llm_slot() -> None:
    try:
        await asyncio.wait_for(
            get_llm_semaphore().acquire(),
            timeout=get_settings().llm_concurrency_wait_timeout_seconds,
        )
    except TimeoutError as exc:
        raise AIConcurrencyTimeout("AI 服务繁忙，请稍后重试") from exc


def release_llm_slot() -> None:
    get_llm_semaphore().release()
