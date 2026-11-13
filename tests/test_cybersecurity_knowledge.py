"""Integrity and readiness tests for curated cybersecurity knowledge."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "cybersecurity-knowledge"


@pytest.fixture
def knowledge_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN_ROOT))
    stage = importlib.import_module("stage")
    verify_index = importlib.import_module("verify_index")
    return stage, verify_index


def _fixture(tmp_path: Path):
    library = tmp_path / "library"
    priors = tmp_path / "knowledge-priors"
    library.mkdir()
    priors.mkdir()
    content = b"# Container guidance\n\nBounded reference content.\n"
    (library / "container.md").write_bytes(content)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus_id": "omega-cybersecurity-reference",
                "corpus_version": "test",
                "knowledge_class": "reference_guidance",
                "documents": [
                    {
                        "path": "container.md",
                        "title": "Container guidance",
                        "reviewed": "2026-11-12",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "sources": ["https://docs.docker.com/engine/security/"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return library, manifest, priors


def test_repository_manifest_covers_reviewed_library(knowledge_modules):
    stage, _ = knowledge_modules
    entries = stage.documents()
    assert len(entries) == 6
    assert {entry[0].name for entry in entries} == {
        "assessment-and-evidence-contract.md",
        "authorization-and-agent-boundaries.md",
        "container-isolation-and-hardening.md",
        "evidence-integrity-and-reproducibility.md",
        "incident-response-and-recovery.md",
        "linux-host-assessment.md",
    }


def test_stage_preserves_unowned_priors_and_detects_tampering(
    knowledge_modules, tmp_path
):
    stage, _ = knowledge_modules
    library, manifest, priors = _fixture(tmp_path)
    unrelated = priors / "operator-notes.md"
    unrelated.write_text("keep", encoding="utf-8")

    assert stage.stage(library, manifest, priors) == 1
    target = priors / "omega-cybersec--container.md"
    assert target.read_bytes() == (library / "container.md").read_bytes()
    assert unrelated.read_text(encoding="utf-8") == "keep"

    target.chmod(0o644)
    target.write_text("altered", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        stage.verify(library, manifest, priors)


def test_manifest_rejects_unreviewed_markdown(knowledge_modules, tmp_path):
    stage, _ = knowledge_modules
    library, manifest, _ = _fixture(tmp_path)
    (library / "unreviewed.md").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest/library mismatch"):
        stage.documents(library, manifest)


def test_index_readiness_requires_current_receipt_and_chunk(
    knowledge_modules, tmp_path
):
    stage, verify_index = knowledge_modules
    library, manifest, priors = _fixture(tmp_path)
    stage.stage(library, manifest, priors)
    target = priors / "omega-cybersec--container.md"
    digest = hashlib.md5(target.read_bytes()).hexdigest()

    class Collection:
        def __init__(self, receipt=digest, has_chunk=True):
            self.receipt = receipt
            self.has_chunk = has_chunk

        def get(self, ids=None, where=None, include=None):
            if ids:
                return {"metadatas": [{"hash": self.receipt}]}
            return {"metadatas": [{"type": "chunk"}] if self.has_chunk else []}

    assert not verify_index.missing_documents(
        Collection(), library, manifest, priors
    )
    assert verify_index.missing_documents(
        Collection(receipt="stale"), library, manifest, priors
    ) == [target.name]
    assert verify_index.missing_documents(
        Collection(has_chunk=False), library, manifest, priors
    ) == [target.name]
