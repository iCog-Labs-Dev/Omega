"""Bounded cybersecurity knowledge retrieval for specialist delegations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from subagents import KnowledgeProjection, KnowledgeReference


MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent
    / "cybersecurity-knowledge"
    / "manifest.json"
)
SOURCE_PREFIX = "omega-cybersec--"
MAX_REFERENCES = 3
MAX_EXCERPT_CHARS = 1000
MIN_RETRIEVAL_CHARS = 400

_CYBERSECURITY_TERMS = re.compile(
    r"\b(?:security|secure|cyber|vulnerab\w*|threat\w*|attack\w*|"
    r"incident\w*|exploit\w*|malware|credential\w*|secret\w*|"
    r"permission\w*|hardening|harden|isolation|forensic\w*|"
    r"remediat\w*|compromis\w*|cve(?:-\d+)*|risk assessment)\b",
    re.IGNORECASE,
)

_ROLE_HINTS = {
    "research-agent": (
        "Identify security weaknesses, authorized assessment boundaries, and "
        "evidence needed to substantiate findings."
    ),
    "coding-agent": (
        "Apply minimal security remediation, preserve required functionality, "
        "and keep changes reversible."
    ),
    "reviewer-agent": (
        "Independently verify security claims, functionality, evidence integrity, "
        "and unresolved risk."
    ),
}


def _manifest(path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (
        raw.get("schema_version") != 1
        or raw.get("corpus_id") != "omega-cybersecurity-reference"
        or raw.get("knowledge_class") != "reference_guidance"
    ):
        raise ValueError("unsupported cybersecurity knowledge manifest")
    result: dict[str, dict[str, Any]] = {}
    for entry in raw.get("documents", ()):
        relative = str(entry.get("path", ""))
        digest = str(entry.get("sha256", ""))
        if (
            not relative.endswith(".md")
            or "/" in relative
            or "\\" in relative
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("invalid cybersecurity knowledge manifest entry")
        title = str(entry.get("title", "")).strip()
        reviewed = str(entry.get("reviewed", "")).strip()
        if not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reviewed):
            raise ValueError("invalid cybersecurity knowledge provenance")
        source = SOURCE_PREFIX + relative
        result[source] = {
            "source_id": relative,
            "title": title,
            "sha256": digest,
            "reviewed": reviewed,
        }
    if not result:
        raise ValueError("cybersecurity knowledge manifest is empty")
    return result


def _default_dependencies():
    from config import config_get_by_key
    import rag

    provider = str(config_get_by_key("embeddingprovider", "Local")).strip()
    if provider.casefold() == "local":
        embedder = rag.local_embed_batch
    elif provider.casefold() in {"asicloud", "openai"}:
        embedder = rag.cloud_embed_batch
    else:
        raise ValueError(f"unsupported embedding provider: {provider}")
    return rag._get_collection(), embedder


def retrieve_for_subagent(
    goal: str,
    agent_id: str,
    max_chars: int,
    *,
    collection: Any = None,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    manifest_path: Path = MANIFEST_PATH,
) -> KnowledgeProjection:
    """Retrieve approved excerpts using only the bounded goal as the query."""
    goal = str(goal).strip()
    if not _CYBERSECURITY_TERMS.search(goal):
        return KnowledgeProjection(status="not_applicable")
    if max_chars < MIN_RETRIEVAL_CHARS:
        return KnowledgeProjection(status="skipped_no_budget")

    sources = _manifest(manifest_path)
    query = f"{goal[:2000]}\n\nRole focus: {_ROLE_HINTS.get(agent_id, '')}".strip()
    query_sha256 = hashlib.sha256(query.encode("utf-8")).hexdigest()
    if collection is None or embedder is None:
        default_collection, default_embedder = _default_dependencies()
        if collection is None:
            collection = default_collection
        if embedder is None:
            embedder = default_embedder

    embeddings = embedder([query])
    if len(embeddings) != 1 or not embeddings[0]:
        raise RuntimeError("knowledge query embedding was empty")
    response = collection.query(
        query_embeddings=embeddings,
        n_results=MAX_REFERENCES * 2,
        where={
            "$and": [
                {"type": {"$eq": "chunk"}},
                {"source": {"$in": sorted(sources)}},
            ]
        },
        include=["documents", "metadatas", "distances"],
    )

    ids = (response.get("ids") or [[]])[0]
    documents = (response.get("documents") or [[]])[0]
    metadatas = (response.get("metadatas") or [[]])[0]
    available = max_chars - 300
    references = []
    seen = set()
    for chunk_id, document, metadata in zip(ids, documents, metadatas):
        metadata = metadata or {}
        source = str(metadata.get("source", ""))
        approved = sources.get(source)
        breadcrumb = str(metadata.get("breadcrumb", source)).strip()
        identity = (source, breadcrumb)
        if approved is None or identity in seen or available <= 0:
            continue
        seen.add(identity)
        excerpt = str(document or "").strip()
        if not excerpt:
            continue
        excerpt = excerpt[: min(MAX_EXCERPT_CHARS, available)]
        references.append(
            KnowledgeReference(
                **approved,
                chunk_id=str(chunk_id),
                breadcrumb=breadcrumb,
                excerpt=excerpt,
            )
        )
        available -= len(excerpt)
        if len(references) == MAX_REFERENCES:
            break

    return KnowledgeProjection(
        status="retrieved" if references else "empty",
        query_sha256=query_sha256,
        references=tuple(references),
    )
