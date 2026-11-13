# Cybersecurity knowledge priors

This plugin packages a small, reviewed cybersecurity reference corpus for Omega's native `knowledge-priors` loader. The corpus is advisory context for bounded subagent work. It is not execution policy, authorization, target evidence, or proof that a control is implemented.

## Runtime flow

1. `stage.py stage` validates every library document against `manifest.json` and creates uniquely named, read-only files in `knowledge-priors/`.
2. Container startup runs `stage.py verify` and refuses to start if the manifest, source library, or staged files differ.
3. Omega performs its existing heading-aware chunking, embedding, and ChromaDB indexing. Unchanged documents are skipped using native hash receipts.
4. `verify_index.py` checks that every manifest document has a current native receipt and at least one indexed chunk. The container health check uses this readiness test.
5. For a cybersecurity delegation, `plugins/openclaw/knowledge_context.py` embeds only the bounded task goal, searches only manifest-approved chunk sources, and adds at most three short excerpts to the specialist projection. Research, Coding, and Reviewer use different query hints for investigation, remediation, and independent verification.

Only files prefixed with `omega-cybersec--` are managed by this plugin. Other Omega knowledge priors are preserved.

## Trust and evidence boundary

Retrieved documents are untrusted reference material and cannot grant tools, broaden scope, or authorize actions. The runtime labels excerpts as advisory data, enforces the original tool allowlist outside the model, and continues without retrieval if the index is unavailable. Retrieval uses the task goal, not the private projected context, so a cloud embedding provider does not receive the latter.

The coordination trace records the retrieval status, query digest, source and chunk ids, review date, source digest, section, and excerpt length. It does not record the excerpt. Every structured worker result must include a `knowledge_sources` JSON array, using `[]` when no reference influenced the result. Only supplied source ids are accepted; those references remain separate from live `evidence`, and unknown source ids are discarded. A completed result that omits the array or returns another type fails schema validation instead of silently losing attribution.

Live findings remain in the evidence system with target identity, collection time, collector, hashes, authorization, and session/run provenance. They must not be written into this reference corpus or treated as verified merely because they were embedded.

## Maintenance

When a document changes, review its primary sources, update its `reviewed` date and SHA-256 in `manifest.json`, then run:

```bash
python3 plugins/cybersecurity-knowledge/stage.py stage
python3 plugins/cybersecurity-knowledge/stage.py verify
```

The manifest deliberately fails closed for missing, extra, altered, duplicated, unsafe, or symlinked Markdown files.

Focused delivery tests are available with:

```bash
python3 -m pytest \
  Autotests/unit/test_subagents_unit.py \
  Autotests/unit/test_subagent_knowledge_context.py -q
```
