"""Validate and stage the curated cybersecurity corpus for Omega's loader."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import date
from pathlib import Path, PurePosixPath


PLUGIN_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PLUGIN_ROOT.parent.parent
LIBRARY = PLUGIN_ROOT / "library"
MANIFEST = PLUGIN_ROOT / "manifest.json"
PRIORS = REPO_ROOT / "knowledge-priors"
PREFIX = "omega-cybersec--"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("Manifest document paths must be non-empty strings")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe manifest document path: {value!r}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Knowledge document must be Markdown: {value!r}")
    return path


def _staged_name(path: PurePosixPath) -> str:
    return PREFIX + "--".join(path.parts)


def documents(library: Path = LIBRARY, manifest_path: Path = MANIFEST):
    library = library.resolve(strict=True)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("Unsupported cybersecurity manifest schema")
    if raw.get("corpus_id") != "omega-cybersecurity-reference":
        raise ValueError("Unexpected cybersecurity corpus id")
    if not isinstance(raw.get("corpus_version"), str) or not raw["corpus_version"]:
        raise ValueError("Cybersecurity corpus version is missing")
    if raw.get("knowledge_class") != "reference_guidance":
        raise ValueError("Cybersecurity corpus must remain reference guidance")

    entries = raw.get("documents")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Cybersecurity manifest has no documents")

    seen_paths: set[PurePosixPath] = set()
    seen_names: set[str] = set()
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Cybersecurity manifest entries must be objects")
        relative = _relative_path(entry.get("path"))
        if relative in seen_paths:
            raise ValueError(f"Duplicate manifest path: {relative}")
        seen_paths.add(relative)

        staged_name = _staged_name(relative)
        if staged_name in seen_names:
            raise ValueError(f"Knowledge filename collision: {staged_name}")
        seen_names.add(staged_name)

        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise ValueError(f"Invalid SHA-256 for {relative}")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise ValueError(f"Missing document title for {relative}")
        try:
            date.fromisoformat(entry.get("reviewed", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid review date for {relative}") from exc
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources or not all(
            isinstance(source, str) and source.startswith("https://")
            for source in sources
        ):
            raise ValueError(f"Invalid sources for {relative}")

        source = library.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Knowledge document is missing or unsafe: {relative}")
        if not source.resolve().is_relative_to(library):
            raise ValueError(f"Knowledge document escapes the library: {relative}")
        if _sha256(source) != expected_hash:
            raise ValueError(f"Knowledge document hash mismatch: {relative}")
        result.append((source, staged_name, entry))

    actual = {
        path.relative_to(library).as_posix()
        for path in library.rglob("*.md")
        if path.is_file()
    }
    expected = {path.as_posix() for path in seen_paths}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"Manifest/library mismatch; missing={missing}, extra={extra}")
    return result


def verify(
    library: Path = LIBRARY,
    manifest_path: Path = MANIFEST,
    priors: Path = PRIORS,
) -> int:
    entries = documents(library, manifest_path)
    expected = {priors / staged_name for _, staged_name, _ in entries}
    actual = set(priors.glob(f"{PREFIX}*.md")) if priors.is_dir() else set()
    if actual != expected:
        raise ValueError("Staged cybersecurity file set does not match the manifest")
    for source, staged_name, entry in entries:
        target = priors / staged_name
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"Staged knowledge document is unsafe: {staged_name}")
        if _sha256(target) != entry["sha256"] or target.read_bytes() != source.read_bytes():
            raise ValueError(f"Staged knowledge document differs: {staged_name}")
    return len(entries)


def stage(
    library: Path = LIBRARY,
    manifest_path: Path = MANIFEST,
    priors: Path = PRIORS,
) -> int:
    entries = documents(library, manifest_path)
    priors.mkdir(parents=True, exist_ok=True)
    for old in priors.glob(f"{PREFIX}*.md"):
        if old.is_symlink() or not old.is_file():
            raise ValueError(f"Refusing to replace unsafe staged path: {old.name}")
        old.unlink()
    for source, staged_name, _ in entries:
        target = priors / staged_name
        shutil.copyfile(source, target)
        target.chmod(0o444)
    return verify(library, manifest_path, priors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("stage", "verify"))
    args = parser.parse_args()
    count = stage() if args.action == "stage" else verify()
    result = "staged" if args.action == "stage" else "verified"
    print(f"Cybersecurity knowledge: {count} documents {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
