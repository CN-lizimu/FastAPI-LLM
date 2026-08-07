from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables and local env files."""

    app_env: str = "development"
    debug: bool = False
    app_log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    rag_debug_log: bool = False
    rag_debug_max_chars: int = Field(default=800, ge=100, le=5000)

    database_url: str = ""
    db_echo: bool = False
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_timeout_seconds: int = Field(default=30, ge=1)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)

    redis_host: str = "localhost"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: str = ""
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0)
    redis_connect_timeout_seconds: float = Field(default=2.0, gt=0)

    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    cors_allow_credentials: bool = True

    dashscope_api_key: str = ""
    ali_access_key: str = ""
    llm_model_id: str = "qwen3.6-flash"
    dashscope_api_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_endpoint: str = ""
    dashscope_embedding_model: str = "text-embedding-v3"
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_tokens: int = Field(default=1024, ge=1)
    llm_max_retries: int = Field(default=2, ge=0)
    llm_request_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_stream_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_concurrency: int = Field(default=4, ge=1)
    llm_concurrency_wait_timeout_seconds: float = Field(default=5.0, gt=0)

    chroma_collection_name: str = "news_rag_cosine_candidate"
    chroma_legacy_collection_name: str = "news_rag"
    chroma_cosine_candidate_collection_name: str = "news_rag_cosine_candidate"
    chroma_persist_dir: str = str(BASE_DIR / "chroma_db")
    chroma_distance_metric: Literal["cosine", "l2", "ip"] = "cosine"
    rag_chunk_size: int = Field(default=500, ge=50)
    rag_chunk_overlap: int = Field(default=100, ge=0)
    rag_top_k: int = Field(default=5, ge=1, le=50)
    rag_score_threshold: float = Field(default=0.5, ge=-1, le=1)
    rag_retrieval_timeout_seconds: float = Field(default=30.0, gt=0)
    rag_db_fetch_batch_size: int = Field(default=5, ge=1)
    rag_write_batch_size: int = Field(default=5, ge=1)
    rag_add_retry_attempts: int = Field(default=3, ge=1)
    rag_add_retry_delay_seconds: float = Field(default=2.0, ge=0)
    rag_start_offset: int = Field(default=0, ge=0)
    rag_recreate_collection: bool = False
    rag_dry_run: bool = False
    chat_history_limit: int = Field(default=10, ge=1, le=100)

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "fastapi-llm"
    jwt_audience: str = "fastapi-llm-users"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=1)
    jwt_refresh_token_expire_days: int = Field(default=14, ge=1)

    model_config = SettingsConfigDict(
        env_file=(str(BASE_DIR / ".env"), str(BASE_DIR / ".env.local")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def database_dsn(self) -> str:
        if not self.database_url:
            raise RuntimeError("服务端未配置 DATABASE_URL，无法连接数据库")
        return self.database_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def chroma_persist_directory(self) -> str:
        path = Path(self.chroma_persist_dir)
        return str(path if path.is_absolute() else BASE_DIR / path)

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
