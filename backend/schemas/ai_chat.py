from typing import Literal, Optional

from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    


class AIChatRequest(BaseModel):
    # 兼容旧前端字段。后端实际调用模型统一读取 .env 的 CHAT_MODEL，不再信任请求体 model。
    model: Optional[str] = None
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = True
    session_id: Optional[str] = Field(default=None, description="对话ID")

class ChatSessionResponse(BaseModel):
    session_id: str
    title: Optional[str] = None
    summary: Optional[str] = None

class ChatSessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
