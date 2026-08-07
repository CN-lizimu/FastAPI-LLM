from __future__ import annotations

import argparse
import json
import time

import chromadb

from config.settings import get_settings


def _collection_names(client) -> set[str]:
    return {collection.name for collection in client.list_collections()}


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Clone an existing Chroma collection into an isolated cosine candidate."
    )
    parser.add_argument("--source", default=settings.chroma_legacy_collection_name)
    parser.add_argument("--target", default=settings.chroma_cosine_candidate_collection_name)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--recreate-target", action="store_true")
    args = parser.parse_args()

    if args.source == args.target:
        raise ValueError("source 和 target 不能相同")

    client = chromadb.PersistentClient(path=settings.chroma_persist_directory)
    names = _collection_names(client)
    if args.source not in names:
        raise RuntimeError(f"源集合不存在: {args.source}")

    if args.target in names:
        if not args.recreate_target:
            raise RuntimeError(
                f"候选集合已存在: {args.target}。如需重建，仅对目标集合使用 --recreate-target。"
            )
        client.delete_collection(args.target)

    source = client.get_collection(args.source)
    target = client.create_collection(
        args.target,
        metadata={
            "hnsw:space": "cosine",
            "source_collection": args.source,
            "build_method": "clone_existing_embeddings",
        },
    )

    started = time.perf_counter()
    source_count = source.count()
    copied = 0
    for offset in range(0, source_count, args.batch_size):
        batch = source.get(
            limit=args.batch_size,
            offset=offset,
            include=["documents", "metadatas", "embeddings"],
        )
        embeddings = batch.get("embeddings")
        if embeddings is not None and hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()
        target.add(
            ids=batch["ids"],
            documents=batch["documents"],
            metadatas=batch["metadatas"],
            embeddings=embeddings,
        )
        copied += len(batch["ids"])

    target_count = target.count()
    if target_count != source_count:
        raise RuntimeError(f"复制数量不一致: source={source_count}, target={target_count}")

    print(
        json.dumps(
            {
                "source_collection": args.source,
                "target_collection": args.target,
                "persist_directory": settings.chroma_persist_directory,
                "source_count": source_count,
                "copied_count": copied,
                "target_count": target_count,
                "target_metadata": target.metadata,
                "target_configuration": target.configuration,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
