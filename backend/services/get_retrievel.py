from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from services.model_factory import get_chat_model
from utils.create_prompt import load_template_text


RETRIEVAL_PROMPT_TEMPLATE_PATH = "config/retrievel_rag_sysytem.txt"

_CONTEXT_DEPENDENT_MARKERS = (
    "它",
    "它们",
    "他们",
    "她们",
    "这个",
    "这件事",
    "这项",
    "上述",
    "前面",
    "刚才",
    "其中",
    "对此",
    "该事件",
    "该公司",
    "该国",
    "上一个",
    "继续说",
    "还有吗",
    "后来呢",
)


def needs_query_rewrite(query: str, recent_messages: list) -> bool:
    """Rewrite only when history can resolve an explicit contextual reference."""
    if not recent_messages:
        return False
    normalized = "".join((query or "").split()).lower()
    return any(marker in normalized for marker in _CONTEXT_DEPENDENT_MARKERS)


def get_retrievel_chain():
    retrievel_system_prompt = load_template_text(RETRIEVAL_PROMPT_TEMPLATE_PATH)
    chat_model = get_chat_model()

    retrieval_query_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", retrievel_system_prompt),
            MessagesPlaceholder(variable_name="recent_messages"),
            (
                "human",
                "当前用户问题：{query}",
            ),
        ]
    )

    return retrieval_query_prompt | chat_model | StrOutputParser()
