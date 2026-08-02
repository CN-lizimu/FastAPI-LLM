from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Central application settings loaded from backend/.env and environment."""

    dashscope_api_key: str = ""
    ali_access_key: str = ""
    llm_model_id: str = "qwen3.6-flash"
    dashscope_api_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_endpoint: str = ""
    dashscope_embedding_model: str = "text-embedding-v3"
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "fastapi-llm"
    jwt_audience: str = "fastapi-llm-users"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 14

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def dashscope_key(self) -> str:
        return self.dashscope_api_key or self.ali_access_key

    @property
    def chat_model_id(self) -> str:
        return self.llm_model_id

    @property
    def chat_completions_endpoint(self) -> str:
        if self.dashscope_api_endpoint:
            return self.dashscope_api_endpoint
        return f"{self.dashscope_api_base_url.rstrip('/')}/chat/completions"

    @property
    def jwt_key(self) -> str:
        if self.jwt_secret_key:
            return self.jwt_secret_key
        raise RuntimeError("服务端未配置 JWT_SECRET_KEY，无法签发或验证 JWT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
