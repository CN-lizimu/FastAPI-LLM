from functools import lru_cache

from langchain_community.chat_models import ChatOpenAI

from config.settings import get_settings


def get_chat_model_name() -> str:
    return get_settings().chat_model_id


def get_dashscope_api_key() -> str:
    return get_settings().dashscope_key


def get_dashscope_chat_endpoint() -> str:
    return get_settings().chat_completions_endpoint


@lru_cache(maxsize=1)
def get_chat_model() -> ChatOpenAI:
    """Get a singleton OpenAI-compatible chat model instance via DashScope."""
    api_key = get_dashscope_api_key()
    if not api_key:
        raise RuntimeError("服务端未配置 DASHSCOPE_API_KEY，无法初始化 LangChain Chat 模型")

    return ChatOpenAI(
        model=get_chat_model_name(),
        api_key=api_key,
        base_url=get_settings().dashscope_api_base_url,
        streaming=True,
    )
