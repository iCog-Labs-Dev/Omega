# Three-agent orchestration benchmark

Measures whether a main agent can coordinate two collaborators in one shared channel:
split the work, brief each collaborator, catch a wrong result, and fold the accepted
parts into one answer — without spamming the channel or looping.

This is not a pass/fail suite. Runs cost provider money and produce a graded transcript,
so `bench/` is kept out of `Autotests/`.

## What is here

| Path | Purpose |
|---|---|
| `bus.py` | One channel, N parties. HTTP server plus the client calls agents make. Writes the transcript. |
| `benchchannel.py` | The `bench` comm channel, registered in `config/plugins.yaml`. Joins one agent to the bus. |
| `roles/` | Role prompts. The framed main agent is `main_plain.txt` + `frame.txt`, so configurations differ by the frame and nothing else. |
| `tasks/` | One YAML per task: prompt, deliverables, work packages, deterministic checks, evaluator key, perturbation. |
| `runner.py` | Runs trials: renders roles, launches containers, posts the task, watches for the final answer, writes `run.json`. |
| `score.py` | Deterministic scoring: the task's checks against the published answer, plus the log-derived orchestration metrics. Writes `score.json`. |
| `judge.py` | One model call per trial for the rubric lines that need reading, with the evaluator key and the deterministic results supplied as authoritative. Writes `judge.json`. |
| `report.py` | Medians the trials of each configuration, compares orchestration against the single-agent baseline, and applies the retention bands. Writes `report.md`. |
| `smoke.sh` | Two agents, one exchange. Run this first when anything in the plumbing changes. |
| `test_*.py` | Host-only tests. No Docker, no LLM, no API key.

## Setup

```sh
docker build -t omegaclaw:bench .          # bench/ ships inside the image
python3 -m venv .venv && .venv/bin/pip install pytest pyyaml
```

An `.env` with the provider key is required, the same one the agent normally uses; the
container's proxy reads it, and the agent process never sees it.

## Running

```sh
.venv/bin/python -m pytest bench/                       # host checks, free and instant
bench/smoke.sh                                          # plumbing check, two live agents

bench/runner.py --tasks qr1 --configs framed --trials 1
bench/runner.py --tasks qr1 --configs framed,plain,solo --trials 3
bench/runner.py --tasks qr1 --perturb                   # inject the scripted bad result
bench/runner.py --full                                  # every task, every config, 3 trials
```

Configurations: `framed` (main agent keeps a context frame via `pin`), `plain` (same
agents, no frame), `solo` (one agent, no collaborators).

Then score, judge, and report:

```sh
bench/score.py --all                                    # free, no model calls
bench/judge.py --all                                    # one model call per trial
bench/judge.py --dry-run bench/runs/qr1/framed/trial-1   # see the judge's prompt
bench/report.py --json bench/runs/results.json
```

Results land in `bench/runs/<task>/<config>/trial-<n>/`:

```
transcript.jsonl        every message, ordered, with its author — the scoring artifact
run.json                metadata, stop reason, final answer, message count, timings
score.json              per-check results, orchestration metrics, flags
judge.json              per-rubric-line points with justifications
agents/<name>/memory/   that agent's role prompt, history, vector store
agents/<name>/docker.log
```

A trial ends when the main agent publishes a message beginning `FINAL ANSWER`, or at the
message cap (default 24), the wall clock (default 900 s), or an agent dying.

## Notes for anyone changing this

- **Containers need `-it`.** Without a TTY the container's nginx cannot open
  `/dev/stderr` and the agent exits before its loop starts.
- **Wait on `CHARS_SENT: <digits>`.** The bare string appears in the MeTTa source dump at
  startup, so matching it alone reports a dead container as ready.
- **Per-run container names and bus port.** With fixed names, a leftover run adopts this
  run's containers and posts into its bus.
- **The channel interface differs between branches**: `start()`/`stop()` plus
  `config_get_by_key` here, `config(dict)` on older ones.
- **Core broadcasts its version at startup** (`src/loop.metta`), which in a shared channel
  would cost every other agent a turn. `benchchannel.send` drops exactly that message.
- **The task is posted before the containers start**, so the first receive returns it.
  Posting later costs the main agent a turn answering an empty channel.

## Known ceilings

Each is marked in the code with a `ponytail:` comment naming the upgrade path.

- **Token counts are estimates.** The provider's real usage never reaches the log, so
  efficiency uses the `CHARS_SENT:` byte counts in the container logs, divided by four.
  Good for comparing configurations under one provider; not a bill. Measured runs use
  roughly 60,000 estimated prompt tokens for three agents against 5,000 for one — well
  above the source's 3,000-10,000 envelope, because the loop rebuilds the whole prompt
  every iteration.
- **Frames are prompted, not enforced.** The framed configuration asks the main agent to
  keep a `FRAME` block via `pin`; nothing in the runtime makes it. So config 1 versus
  config 2 measures frame discipline, which is a weaker claim than measuring a frame layer
  the loop maintains.
- **Delegation coverage is keyword overlap**, not meaning. The judge confirms it.
- **Role distinctness is lexical** (`difflib`), where the source suggests semantic
  similarity. Borderline cases want a human look, as the source itself says.
- **The judge is a single pass.** No panel, no adversarial second opinion.
- **Position-blind checks.** A `set` check passes if the group appears anywhere in the
  answer, so a right value in the wrong list still passes; the judge catches those.
