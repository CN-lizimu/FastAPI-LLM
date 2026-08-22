from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

import chromadb
from dotenv import load_dotenv
from langchain_community.vectorstores import Chroma


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

# The migration uses the new canonical variable when present while remaining
# compatible with the current Settings field during this first migration stage.
if embedding_model := os.getenv("EMBEDDING_MODEL"):
    os.environ["DASHSCOPE_EMBEDDING_MODEL"] = embedding_model

from config.chroma_conf import IngestConfig, load_config_from_env
from config.db_conf import AsyncSessionLocal, async_engine
from config.settings import get_settings
from services.RAG_chroma.add_to_chroma import (
    _add_documents_with_retry,
    _build_documents_for_news,
    _build_embedding_model,
    _build_text_splitter,
    _fetch_news_batch,
)


DEFAULT_COLLECTION = os.getenv("RAG_COLLECTION_NAME", "news_rag_qwen37_v1")
PROTECTED_COLLECTIONS = frozenset({"news_rag", "news_rag_cosine_candidate"})
MAX_EMBEDDING_BATCH_SIZE = 20
DEFAULT_EMBEDDING_DIMENSION = 1024


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    defaults = load_config_from_env()
    parser = argparse.ArgumentParser(
        description="Rebuild the news vector store into a protected, independent Chroma collection."
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"Target collection (default: {DEFAULT_COLLECTION}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=defaults.write_batch_size,
        help=f"Embedding/write batch size, 1-{MAX_EMBEDDING_BATCH_SIZE} "
        f"(default: {defaults.write_batch_size}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and split all news without creating a collection or calling the embedding API.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    collection = args.collection.strip()
    if not collection:
        raise SystemExit("Collection name cannot be empty.")
    if collection in PROTECTED_COLLECTIONS:
        protected = ", ".join(sorted(PROTECTED_COLLECTIONS))
        raise SystemExit(
            f"Refusing to modify protected collection '{collection}'. Protected: {protected}"
        )
    if not 1 <= args.batch_size <= MAX_EMBEDDING_BATCH_SIZE:
        raise SystemExit(
            f"--batch-size must be between 1 and {MAX_EMBEDDING_BATCH_SIZE}."
        )
    args.collection = collection


async def _scan_source(config: IngestConfig) -> tuple[int, int]:
    splitter = _build_text_splitter(config)
    news_count = 0
    chunk_count = 0
    offset = 0

    async with AsyncSessionLocal() as db:
        while True:
            news_batch = await _fetch_news_batch(
                db,
                offset=offset,
                limit=config.db_fetch_batch_size,
            )
            if not news_batch:
                break

            for news in news_batch:
                documents, _ = _build_documents_for_news(news, splitter)
                news_count += 1
                chunk_count += len(documents)

            offset += len(news_batch)

    return news_count, chunk_count


def _build_target_vector_store(config: IngestConfig) -> Chroma:
    settings = get_settings()
    client = chromadb.PersistentClient(path=config.persist_directory)
    existing = {collection.name for collection in client.list_collections()}
    if config.collection_name in existing:
        logging.info("Deleting target collection before rebuild: %s", config.collection_name)
        client.delete_collection(config.collection_name)

    return Chroma(
        collection_name=config.collection_name,
        embedding_function=_build_embedding_model(),
        persist_directory=config.persist_directory,
        collection_metadata={
            "hnsw:space": config.distance_metric,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": DEFAULT_EMBEDDING_DIMENSION,
            "chunk_size": config.chunk_size,
            "chunk_overlap": config.chunk_overlap,
            "source": "mysql.news",
            "build_version": "qwen37_v1",
        },
    )


async def _flush_documents(
    *,
    vector_store,
    documents: Sequence,
    ids: Sequence[str],
    config: IngestConfig,
    logger: logging.Logger,
) -> None:
    if not documents:
        return
    await _add_documents_with_retry(
        vector_store,
        documents,
        ids,
        config,
        logger,
    )


async def _rebuild_collection(
    config: IngestConfig,
    *,
    expected_news: int,
    expected_chunks: int,
) -> tuple[int, int]:
    logger = logging.getLogger("news_vector_rebuild")
    splitter = _build_text_splitter(config)
    vector_store = _build_target_vector_store(config)

    pending_documents: list = []
    pending_ids: list[str] = []
    processed_news = 0
    written_chunks = 0
    offset = 0

    async with AsyncSessionLocal() as db:
        while True:
            news_batch = await _fetch_news_batch(
                db,
                offset=offset,
                limit=config.db_fetch_batch_size,
            )
            if not news_batch:
                break

            for news in news_batch:
                documents, ids = _build_documents_for_news(news, splitter)
                vector_store.delete(where={"news_id": news.id})
                pending_documents.extend(documents)
                pending_ids.extend(ids)
                processed_news += 1

                while len(pending_documents) >= config.write_batch_size:
                    batch_documents = pending_documents[: config.write_batch_size]
                    batch_ids = pending_ids[: config.write_batch_size]
                    del pending_documents[: config.write_batch_size]
                    del pending_ids[: config.write_batch_size]
                    await _flush_documents(
                        vector_store=vector_store,
                        documents=batch_documents,
                        ids=batch_ids,
                        config=config,
                        logger=logger,
                    )
                    written_chunks += len(batch_documents)
                    logger.info(
                        "Write progress: news=%s/%s, chunks=%s/%s",
                        processed_news,
                        expected_news,
                        written_chunks,
                        expected_chunks,
                    )

            offset += len(news_batch)

    if pending_documents:
        await _flush_documents(
            vector_store=vector_store,
            documents=pending_documents,
            ids=pending_ids,
            config=config,
            logger=logger,
        )
        written_chunks += len(pending_documents)

    logger.info(
        "Write progress: news=%s/%s, chunks=%s/%s",
        processed_news,
        expected_news,
        written_chunks,
        expected_chunks,
    )
    return processed_news, written_chunks


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    base_config = load_config_from_env()
    config = replace(
        base_config,
        collection_name=args.collection,
        write_batch_size=args.batch_size,
        start_offset=0,
        recreate_collection=not args.dry_run,
        dry_run=args.dry_run,
    )

    news_count, chunk_count = await _scan_source(config)
    print(f"News count: {news_count}")
    print(f"Chunk count: {chunk_count}")
    print(f"Embedding model: {settings.embedding_model}")
    print(f"Collection: {config.collection_name}")
    print(f"Persist directory: {config.persist_directory}")
    print(f"Batch size: {config.write_batch_size}")

    if args.dry_run:
        print("Dry run complete: no collection was created and no embedding API was called.")
        return

    processed_news, written_chunks = await _rebuild_collection(
        config,
        expected_news=news_count,
        expected_chunks=chunk_count,
    )
    print(
        "Rebuild complete: "
        f"news={processed_news}, chunks={written_chunks}, collection={config.collection_name}"
    )


def main() -> None:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()
    _validate_args(args)

    async def run() -> None:
        try:
            await _main(args)
        finally:
            await async_engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
