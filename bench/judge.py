"""Score the parts of a trial that need reading rather than counting.

The deterministic scorer settles arithmetic and message bookkeeping; what is left is
judgement — was the decomposition sensible, were collaborator findings restated
faithfully, does the answer own its uncertainty. This asks a model, once per trial, with
the task's evaluator key and the deterministic results in hand so it cannot contradict a
number that was already checked.

    bench/judge.py bench/runs/qr1/framed/trial-1
    bench/judge.py --all --model z-ai/glm-5.2

Writes `judge.json` beside `score.json`, and prints the rubric total. Requires
OPENROUTER_API_KEY, either in the environment or in the .env the agents already use.
"""

import argparse
import json
import os
import re
from pathlib import Path

import openai

import runner

HERE = Path(__file__).resolve().parent
MODEL = "z-ai/glm-5.2"
BASE_URL = "https://openrouter.ai/api/v1"
# The judge asks again, more insistently, if the model ignores response_format and
# returns something json.loads can't parse — not every model behind OpenRouter honors a
# JSON schema as strictly as it's asked to.
MAX_JSON_ATTEMPTS = 3

# The source rubric, section 6. Ids are what the judge returns; the deterministic scorer
# owns the numbers behind them, so a line is judged in light of those numbers, not
# instead of them.
RUBRIC = [
    ("correctness", 25, "Correct conclusions or calculations, judged against the "
                        "evaluator key. The deterministic check results are authoritative "
                        "on any value they cover."),
    ("completeness", 12, "Completeness and compliance with the task's constraints: every "
                         "required deliverable present, limits respected."),
    ("uncertainty", 8, "Appropriate uncertainty, caveats and counterargument: settled "
                       "conclusions separated from contested ones."),
    ("decomposition", 8, "Problem decomposition: were the subtasks the right ones, and "
                         "did they cover the work the task needs?"),
    ("role_distinctness", 5, "Distinct and complementary collaborator roles rather than "
                             "the same request sent twice."),
    ("context_supplied", 5, "Relevant context supplied to each collaborator: the facts, "
                            "expected output and constraints its own part needs."),
    ("integration_accuracy", 8, "Accurate integration of collaborator findings: results "
                                "restated faithfully, not stretched or misattributed."),
    ("critique", 6, "Critique, verification and error correction: were wrong or "
                    "conflicting results caught, checked and resolved?"),
    ("goal_continuity", 3, "Goal and constraint continuity across the run."),
    ("both_useful", 4, "Both collaborators produced useful accepted work."),
    ("efficiency", 6, "Token and turn efficiency: coordination without repeated "
                      "summaries or duplicated calculation."),
    ("traceability", 5, "Traceability of final claims to evidence or collaborator "
                        "results."),
    ("log_hygiene", 5, "Log hygiene, channel discipline and state recording."),
]

# Section 8. Only scored on a perturbed run, and awarded on top of the 100.
PERTURBATION = ("perturbation", 6,
                "Perturbation handling: noticing the contradiction, requesting "
                "verification, locating the exact error, rejecting or revising the faulty "
                "result, keeping the rejected result in the audit history, and preventing "
                "it from entering the final answer. One point each.")

JUDGE_SYSTEM = """You are grading one run of a benchmark that measures how well a main
agent coordinates two collaborator agents in a single shared channel.

Grade only what the transcript and final answer show. The deterministic results supplied
with each run were computed in code and are authoritative: never award correctness points
for a value a check marked failed, and never deduct for one it marked passed.

Award whole points per rubric line, from 0 to that line's maximum. Justify each in one
sentence that cites what in the run earned or lost the points. Be a strict grader: a line
earns its maximum only when the run does that thing well, not merely adequately."""


def _schema(lines):
    return {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string", "enum": [name for name, _, _ in lines]},
                        "points": {"type": "integer"},
                        "justification": {"type": "string"},
                    },
                    "required": ["metric", "points", "justification"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["scores", "summary"],
        "additionalProperties": False,
    }


def build_prompt(task, run, score, lines):
    """Everything the judge is allowed to consider, in one message."""
    transcript = "\n\n".join(f"[{m['seq']}] {m['agent']}: {m['text']}"
                             for m in run["messages"])
    checks = "\n".join(f"- {c['label']}: {'PASSED' if c['passed'] else 'FAILED'} ({c['detail']})"
                       for c in score["checks"])
    rubric = "\n".join(f"- {name} (max {maximum}): {description}"
                       for name, maximum, description in lines)
    agents = ", ".join(f"{a['name']}{' (scripted, not a real agent)' if a['puppet'] else ''}"
                       for a in run["agents"])

    parts = [
        f"# Task given to the agents\n\n{task['prompt']}",
        f"# Required deliverables\n\n" + "\n".join(f"- {d}" for d in task["deliverables"]),
        f"# Evaluator key\n\n{task['key']}",
        "# Documented failure conditions\n\n" + "\n".join(f"- {f}" for f in task["failure_conditions"]),
        f"# Configuration\n\nconfig: {run['config']} | agents: {agents} | "
        f"stop reason: {run['stop_reason']} | messages: {run['message_count']}",
    ]
    if run.get("puppet_reply"):
        parts.append("# Injected error\n\nOne collaborator was replaced by this scripted, "
                     f"deliberately faulty result:\n\n{run['puppet_reply']}\n\n"
                     f"A successful run recovers as follows:\n\n"
                     f"{task['perturbation']['expected_recovery']}")
    parts += [
        f"# Deterministic check results (authoritative)\n\n{checks}",
        f"# Deterministic orchestration metrics\n\n```json\n"
        f"{json.dumps(score['metrics'], indent=2)}\n```",
        f"# The channel transcript\n\n{transcript}",
        f"# The published final answer\n\n{run.get('final_answer') or '(none was published)'}",
        f"# Rubric\n\n{rubric}\n\nScore every line above, then summarise the run in two "
        f"sentences.",
    ]
    return "\n\n".join(parts)


def _extract_json(text):
    """Parse a model's JSON reply, tolerating stray prose around the object.

    response_format is a request, not a guarantee, once it passes through OpenRouter to
    whatever model is behind it — this is the fallback for a model that wraps the JSON in
    a sentence or a code fence instead of returning it bare.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group())


def _call_judge(client, model, prompt, schema):
    """One structured-output call, retried if the model doesn't return parseable JSON."""
    messages = [{"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": prompt}]
    last_error = None
    # OpenAI's current models reject max_tokens and want max_completion_tokens; OpenRouter
    # takes either. Ask for the new name first and fall back once, so both endpoints work.
    cap = {"max_completion_tokens": 16000}
    for attempt in range(MAX_JSON_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, **cap,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "verdict", "schema": schema, "strict": True}},
            )
        except openai.BadRequestError:
            if "max_tokens" in cap:
                raise
            cap = {"max_tokens": 16000}
            continue
        text = response.choices[0].message.content
        try:
            return _extract_json(text), response.usage
        except (json.JSONDecodeError, AttributeError) as error:
            last_error = error
            messages.append({"role": "user", "content":
                             "That was not a single valid JSON object matching the schema. "
                             "Respond with ONLY the JSON object — no prose, no code fence."})
    raise RuntimeError(f"the judge never returned parseable JSON: {last_error}")


def judge_trial(trial_dir, client, model=MODEL):
    trial_dir = Path(trial_dir)
    run = json.loads((trial_dir / "run.json").read_text())
    score = json.loads((trial_dir / "score.json").read_text())
    task = runner.load_task(run["task"])
    lines = RUBRIC + ([PERTURBATION] if run.get("puppet_reply") else [])

    verdict, usage = _call_judge(client, model, build_prompt(task, run, score, lines),
                                 _schema(lines))
    result = collect(verdict, lines)
    result.update({
        "task": run["task"], "config": run["config"], "trial": run["trial"],
        "model": model, "summary": verdict["summary"],
        "usage": {"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens},
    })
    (trial_dir / "judge.json").write_text(json.dumps(result, indent=2))
    return result


def collect(verdict, lines):
    """Clamp each score to its line's maximum and total them, noting anything missing."""
    maximums = {name: maximum for name, maximum, _ in lines}
    awarded, missing = {}, []
    for entry in verdict["scores"]:
        name = entry["metric"]
        if name in maximums:
            awarded[name] = {"points": max(0, min(entry["points"], maximums[name])),
                             "max": maximums[name],
                             "justification": entry["justification"]}
    missing = [name for name in maximums if name not in awarded]

    rubric_total = sum(v["points"] for name, v in awarded.items() if name != "perturbation")
    return {
        "scores": awarded,
        "missing_lines": missing,
        "rubric_total": rubric_total,
        "rubric_max": sum(m for name, m, _ in lines if name != "perturbation"),
        "perturbation_points": awarded.get("perturbation", {}).get("points"),
    }


def api_key(names=("OPENROUTER_API_KEY", "OPENAI_API_KEY")):
    """The first of `names` set in the environment, or in the .env the agents already use.

    Two names because the judge can run against either OpenRouter or OpenAI directly, and
    the key that reaches the agents is not always an OpenRouter one.
    """
    env = HERE.parent / ".env"
    text = env.read_text() if env.exists() else ""
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
        found = re.search(rf"^{name}=(.+)$", text, re.M)
        if found:
            return found.group(1).strip().strip("'\"")
    raise SystemExit(f"none of {', '.join(names)} in the environment or .env")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trials", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="judge every scored run")
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL,
                        help="provider endpoint; pass an OpenAI URL to judge without "
                             "an OpenRouter key, at the cost of same-vendor grading")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the prompt that would be sent, and stop")
    args = parser.parse_args()

    trials = args.trials
    if args.all or not trials:
        trials = sorted(p.parent for p in args.runs.rglob("score.json"))

    if args.dry_run:
        for trial in trials:
            run = json.loads((trial / "run.json").read_text())
            score = json.loads((trial / "score.json").read_text())
            lines = RUBRIC + ([PERTURBATION] if run.get("puppet_reply") else [])
            print(build_prompt(runner.load_task(run["task"]), run, score, lines))
        return

    client = openai.OpenAI(api_key=api_key(), base_url=args.base_url)
    for trial in trials:
        result = judge_trial(trial, client, args.model)
        head = f"{result['task']}/{result['config']}/trial-{result['trial']}"
        print(f"{head}: {result['rubric_total']}/{result['rubric_max']}"
              + (f" (+{result['perturbation_points']} perturbation)"
                 if result["perturbation_points"] is not None else ""))
        if result["missing_lines"]:
            print(f"    - not scored: {', '.join(result['missing_lines'])}")


if __name__ == "__main__":
    main()
