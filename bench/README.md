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
| `roles/` | Role prompts. The framed main agent is `main_plain.txt` + `frame.txt`. As of 2026-08-11, `framed` also needs the frame-capable image (see Running) — the role text alone no longer makes the difference. |
| `tasks/` | One YAML per task: prompt, deliverables, work packages, deterministic checks, evaluator key, perturbation. |
| `runner.py` | Runs trials: renders roles, launches containers, posts the task, watches for the final answer, writes `run.json`. Also writes each agent's `docker.clean.log` alongside its raw `docker.log`. |
| `clean_log.py` | Strips a raw `docker.log` down to what a human needs: unescapes the loop's internal `_quote_`/`_newline_`/`_apostrophe_` encoding, collapses the interpreter-startup dump to one line, and unwraps `(RESPONSE: (RESULTS: ((COMMAND_RETURN: ...` nesting. `bench/clean_log.py <dir>` re-cleans every `docker.log` under a run directory. |
| `score.py` | Deterministic scoring: the task's checks against the published answer, plus the log-derived orchestration metrics. Writes `score.json`. |
| `judge.py` | One model call per trial for the rubric lines that need reading, with the evaluator key and the deterministic results supplied as authoritative. Writes `judge.json`. |
| `report.py` | Medians the trials of each configuration, compares orchestration against the single-agent baseline, and applies the retention bands. Writes `report.md`. |
| `smoke.sh` | Two agents, one exchange. Run this first when anything in the plumbing changes. |
| `check_frame_input.metta` | Regression check for the two runtime bugs fixed on 2026-08-12 (see Fixed). Needs Docker and a key; invocation is in its header comment. |
| `test_*.py` | Host-only tests. No Docker, no LLM, no API key.

## Setup

`framed` needs a different image than `plain`/`solo` (see Running), built from two
different branches:

```sh
git worktree add ../omegaclaw-bench-noframe feat/orchestration-benchmark
docker build -t omegaclaw:bench-noframe ../omegaclaw-bench-noframe

docker build -t omegaclaw:bench-frame .    # this branch, cfv2 merged in; bench/ ships inside

python3 -m venv .venv && .venv/bin/pip install pytest pyyaml
```

An `.env` with the provider key is required, the same one the agent normally uses; the
container's proxy reads it, and the agent process never sees it.

## Running

`runner.py` has no shebang and is not executable; call it through the venv interpreter
(`.venv/bin/python bench/runner.py ...`). The bare `bench/runner.py` spelling below is
shorthand for that.

```sh
.venv/bin/python -m pytest bench/                       # host checks, free and instant
bench/smoke.sh                                          # plumbing check, two live agents

bench/runner.py --tasks qr1 --configs plain,solo --image omegaclaw:bench-noframe --trials 3
bench/runner.py --tasks qr1 --configs framed --image omegaclaw:bench-frame --trials 3
bench/runner.py --tasks qr1 --configs framed --image omegaclaw:bench-frame --perturb
bench/runner.py --full --image omegaclaw:bench-noframe --configs plain,solo
bench/runner.py --full --image omegaclaw:bench-frame --configs framed
```

Both invocations write into the same `--out` tree (default `bench/runs/`), keyed by
`<task>/<config>/trial-<n>`, so `report.py` aggregates across them with no changes.

Configurations: `framed` (main agent drives the real context-frame skills — see
`roles/frame.txt`), `plain` (same agents, frame skills don't exist in this image at
all), `solo` (one agent, no collaborators, same no-frame image as `plain`).

**Before 2026-08-11**, `framed` meant something mechanically different: a hand-written
`FRAME` text block re-saved through the `pin` skill, which was a no-op. That convention
ran (and still runs, for the in-flight sweep on `feat/orchestration-benchmark`) on a
single shared image with no real frame layer underneath it. Reports from before that
date use the old meaning; nothing here changes their data.

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

## Results, 2026-08-12

First sweep where all three configurations produced valid data: 72 trials, 8 tasks × 3
configs × 3 trials, OpenAI `gpt-5.6-sol`, no perturbation. 71 of 72 reached a final answer
— the miss is one `framed` message-cap trial. Earlier sweeps each had a broken column, the
worst being `solo` at 0% completion, so nothing before this date is comparable.

| config | rubric /100 | median prompt tokens | vs. solo | completion |
|---|---|---|---|---|
| framed | 89.5 | 79,621 | 19.7× | 23/24 |
| plain | **90.0** | 54,178 | 13.4× | 24/24 |
| solo | 65.5 | 4,041 | 1.0× | 24/24 |

`report.py` prints that as **+23 to +31 for orchestration, retain on all eight tasks**.
Do not quote it. Four rubric lines pay for having collaborators at all — integration
accuracy (8), role distinctness (5), context supplied (5), both useful (4) — so a single
agent scores 0 on 22 of the 100 by construction, not by doing worse. On the 78 points every
config can earn:

| comparison | full rubric | 78 points both can earn |
|---|---|---|
| best orchestrated vs. solo | +24.5 | **+3.0** |
| framed vs. plain | −0.5 | **−0.5** |

Median points per line says where the 3 points come from and what they cost:

| rubric line | max | framed | plain | solo |
|---|---|---|---|---|
| decomposition | 8 | 6.0 | 6.0 | **3.5** |
| critique | 6 | 5.0 | 5.0 | **2.0** |
| correctness | 25 | 23.0 | 24.0 | 24.0 |
| completeness | 12 | 11.0 | 11.0 | **12.0** |
| efficiency | 6 | 3.0 | 3.0 | **5.0** |
| log_hygiene | 5 | 3.0 | 2.0 | **5.0** |
| uncertainty | 8 | 7.5 | 8.0 | 8.0 |
| traceability | 5 | 5.0 | 5.0 | 5.0 |
| goal_continuity | 3 | 3.0 | 3.0 | 3.0 |

The four collaboration-only lines are omitted above: orchestrated configs take all 22,
`solo` takes 0, every time.

Orchestration wins the two lines that describe what it is for — picking the right subtasks,
and catching and resolving wrong results — and loses efficiency, log hygiene and
completeness. Those nearly cancel. It buys better decomposition and error-catching for
13–20× the tokens; it does not buy more correct answers. On the deterministic checks alone
`solo` ties or beats both orchestrated configs on every task except `la3`.

**The frame layer did not pay for itself.** `framed` and `plain` sit within half a point on
both scorings, `plain` leads 5 of 8 tasks, and `framed` spends ~1.5× the tokens. The layer
was active, not idle — median 3 frame calls per trial against 0 elsewhere. `framed` also
owns the worst trial in the sweep, 43/100, against `plain`'s floor of 65.

Read the numbers against these, on top of the ceilings below:

- **The judge was not `z-ai/glm-5.2`.** No OpenRouter key on the host, so it ran on OpenAI
  `gpt-5.5` — same vendor as the model under test, one generation off. Judging is ~1% of
  sweep cost, so re-running it against an independent model is the cheapest way to firm up
  every number here.
- **Two checks look broken.** `qr3`'s concurrency check fails in all 24 trials and cannot
  be satisfied by `solo` at all, which has no second analyst; `qr4`'s "recommends Model B"
  fails in all 24 across every config. Suspect the key, not the models.
- **No prompt caching happened** this run (`cached_tokens=0` throughout), unlike the
  2026-08-11 sweep at ~45% cached. Within-run ratios compare; absolute cost across sweeps
  does not.
- **Three trials per config** is the protocol floor. One or two points between `framed` and
  `plain` is noise. The 22-point structural gap is not.

Worth doing next: an independent judge, a decision on how collaboration-only lines should
score for `solo` (the 78-point subtotal is arguably what `report.py` should print), and the
perturbation sweep — catching a planted wrong result is where orchestration should look
best, and `critique` is already its strongest line.

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
- **Frames are prompted, not enforced — resolved 2026-08-11 for `framed` vs. `plain`.**
  This used to mean the main agent was merely asked to keep a `FRAME` block via `pin`,
  a no-op, so config 1 versus config 2 measured prompt discipline rather than an actual
  frame layer. `framed` now runs on `omegaclaw:bench-frame`, which has a real
  runtime-maintained context frame (`src/context.metta`); `plain`/`solo` run on
  `omegaclaw:bench-noframe`, which has none. The two configs now differ by which image
  they run on as well as by role-prompt text — kept here as a record of the harness's
  evolution, not because the ceiling still applies.
- **Delegation coverage is keyword overlap**, not meaning. The judge confirms it.
- **Role distinctness is lexical** (`difflib`), where the source suggests semantic
  similarity. Borderline cases want a human look, as the source itself says.
- **The judge is a single pass.** No panel, no adversarial second opinion.
- **Position-blind checks.** A `set` check passes if the group appears anywhere in the
  answer, so a right value in the wrong list still passes; the judge catches those.
