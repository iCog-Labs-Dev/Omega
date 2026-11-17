"""Readiness check for the cybersecurity corpus in Omega's native index."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from stage import LIBRARY, MANIFEST, PRIORS, documents, verify


def indexed(collection, staged: Path) -> bool:
    receipt = collection.get(ids=[f"hash_{staged.name}"], include=["metadatas"])
    metadata = receipt.get("metadatas") or []
    expected = hashlib.md5(staged.read_bytes()).hexdigest()
    if not metadata or metadata[0].get("hash") != expected:
        return False
    chunks = collection.get(where={"source": staged.name}, include=["metadatas"])
    return any(item.get("type") == "chunk" for item in chunks.get("metadatas") or [])


def missing_documents(
    collection,
    library: Path = LIBRARY,
    manifest_path: Path = MANIFEST,
    priors: Path = PRIORS,
) -> list[str]:
    verify(library=library, manifest_path=manifest_path, priors=priors)
    return [
        staged_name
        for _, staged_name, _ in documents(library, manifest_path)
        if not indexed(collection, priors / staged_name)
    ]


def main() -> int:
    memory_dir = Path(os.environ.get("MEMORY_DIR", PRIORS.parent / "memory"))
    database = Path(os.environ.get("CHROMA_DB_PATH", memory_dir / "chroma_db"))
    try:
        verify()
        if not (database / "chroma.sqlite3").is_file():
            print(
                f"Cybersecurity knowledge database is not ready: {database}",
                file=sys.stderr,
            )
            return 1
        import chromadb

        collection = chromadb.PersistentClient(path=str(database)).get_collection(
            "memories", embedding_function=None
        )
        missing = missing_documents(collection)
        if missing:
            print(
                "Cybersecurity knowledge is not ready: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 1
        return 0
    except Exception as exc:
        print(f"Cybersecurity knowledge readiness failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
