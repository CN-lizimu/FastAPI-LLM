from dataclasses import dataclass

from config.settings import get_settings


@dataclass(frozen=True)
class IngestConfig:
    collection_name: str
    persist_directory: str
    distance_metric: str
    chunk_size: int
    chunk_overlap: int
    db_fetch_batch_size: int
    write_batch_size: int
    start_offset: int
    add_retry_attempts: int
    add_retry_delay_seconds: float
    recreate_collection: bool
    dry_run: bool


def load_config_from_env() -> IngestConfig:
    """Build one Chroma configuration from the central Settings object."""
    settings = get_settings()
    if settings.rag_chunk_overlap >= settings.rag_chunk_size:
        raise ValueError("RAG_CHUNK_OVERLAP 必须小于 RAG_CHUNK_SIZE")

    return IngestConfig(
        collection_name=settings.rag_collection_name,
        persist_directory=settings.chroma_persist_directory,
        distance_metric=settings.chroma_distance_metric,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        db_fetch_batch_size=settings.rag_db_fetch_batch_size,
        write_batch_size=settings.rag_write_batch_size,
        start_offset=settings.rag_start_offset,
        add_retry_attempts=settings.rag_add_retry_attempts,
        add_retry_delay_seconds=settings.rag_add_retry_delay_seconds,
        recreate_collection=settings.rag_recreate_collection,
        dry_run=settings.rag_dry_run,
    )
