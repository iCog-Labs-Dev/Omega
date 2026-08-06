"""Aggregate scored runs into one readable report.

Collects every trial under a runs directory, medians the trials of each configuration, and
puts the orchestrated configurations next to the single-agent baseline — which is the
comparison the benchmark exists to make. It also checks each task against the source's
retention bands, so a task that no longer discriminates gets flagged rather than quietly
kept.

    bench/report.py                       # writes bench/runs/report.md
    bench/report.py --json results.json   # also emit a machine-readable envelope
"""

import argparse
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Section 3: after pilot testing, keep tasks that separate a single agent from a
# well-orchestrated trio. Outside these bands, the task needs recalibrating.
SOLO_BAND = (55, 80)
ORCHESTRATED_BAND = (80, 95)
ORCHESTRATED = ["framed", "plain"]


def load(runs_dir):
    """Every trial that has been scored, in a flat list."""
    trials = []
    for run_path in sorted(runs_dir.rglob("run.json")):
        trial = {"dir": run_path.parent, "run": json.loads(run_path.read_text())}
        for name in ("score", "judge"):
            path = run_path.parent / f"{name}.json"
            trial[name] = json.loads(path.read_text()) if path.exists() else None
        trials.append(trial)
    return trials


def summarise(trial):
    """One row's worth of numbers, whether or not the judge has run."""
    run, score, judge = trial["run"], trial["score"], trial["judge"]
    return {
        "task": run["task"], "config": run["config"], "trial": run["trial"],
        "judge_total": judge["rubric_total"] if judge else None,
        "perturbation_points": judge["perturbation_points"] if judge else None,
        "checks": f"{score['checks_passed']}/{score['checks_total']}" if score else None,
        "checks_passed": score["checks_passed"] if score else None,
        "checks_total": score["checks_total"] if score else None,
        "messages": run["message_count"],
        "tokens": score["metrics"]["efficiency"]["estimated_prompt_tokens"] if score else None,
        "duration_s": run["duration_s"],
        "stop_reason": run["stop_reason"],
        "perturbed": bool(run.get("puppet_reply")),
        "flags": score["flags"] if score else [],
    }


def median(values):
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 1) if present else None


def by_config(rows):
    """Median score and message count per (task, config)."""
    grouped = {}
    for row in rows:
        grouped.setdefault((row["task"], row["config"]), []).append(row)
    return {key: {"trials": len(group),
                  "judge_median": median([r["judge_total"] for r in group]),
                  "messages_median": median([r["messages"] for r in group]),
                  "tokens_median": median([r["tokens"] for r in group]),
                  "checks_median": median([r["checks_passed"] for r in group]),
                  "checks_total": group[0]["checks_total"]}
            for key, group in grouped.items()}


def retention(task, medians):
    """Section 3: does this task still separate one agent from three?"""
    solo = medians.get((task, "solo"), {}).get("judge_median")
    best = [medians[(task, config)]["judge_median"] for config in ORCHESTRATED
            if (task, config) in medians and medians[(task, config)]["judge_median"] is not None]
    if solo is None or not best:
        return "not enough data", None
    orchestrated = max(best)
    verdict = "retain"
    if not SOLO_BAND[0] <= solo <= SOLO_BAND[1]:
        verdict = f"recalibrate: single agent {solo} outside {SOLO_BAND}"
    elif not ORCHESTRATED_BAND[0] <= orchestrated <= ORCHESTRATED_BAND[1]:
        verdict = f"recalibrate: orchestrated {orchestrated} outside {ORCHESTRATED_BAND}"
    return verdict, round(orchestrated - solo, 1)


def markdown(rows, medians):
    tasks = sorted({row["task"] for row in rows})
    out = ["# Three-agent orchestration benchmark", "",
           f"{len(rows)} trial(s) across {len(tasks)} task(s). "
           f"Scores are out of 100; perturbation points are counted separately.", ""]

    out += ["## Per configuration (median of trials)", "",
            "| task | config | trials | score | checks | messages | est. prompt tokens |",
            "|---|---|---|---|---|---|---|"]
    for (task, config), value in sorted(medians.items()):
        checks = (f"{value['checks_median']}/{value['checks_total']}"
                  if value["checks_median"] is not None else "-")
        out.append(f"| {task} | {config} | {value['trials']} | "
                   f"{value['judge_median'] if value['judge_median'] is not None else 'unjudged'} | "
                   f"{checks} | {value['messages_median']} | {value['tokens_median']} |")

    out += ["", "## Orchestration versus the single-agent baseline", "",
            "| task | single agent | best orchestrated | delta | retention |",
            "|---|---|---|---|---|"]
    for task in tasks:
        verdict, delta = retention(task, medians)
        solo = medians.get((task, "solo"), {}).get("judge_median")
        best = [medians[(task, c)]["judge_median"] for c in ORCHESTRATED
                if (task, c) in medians and medians[(task, c)]["judge_median"] is not None]
        out.append(f"| {task} | {solo if solo is not None else '-'} | "
                   f"{max(best) if best else '-'} | "
                   f"{f'+{delta}' if delta is not None and delta > 0 else delta if delta is not None else '-'} | "
                   f"{verdict} |")

    out += ["", "## Every trial", "",
            "| task | config | trial | score | checks | messages | seconds | stop reason |",
            "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        score = row["judge_total"] if row["judge_total"] is not None else "unjudged"
        if row["perturbation_points"] is not None:
            score = f"{score} (+{row['perturbation_points']} perturbation)"
        out.append(f"| {row['task']} | {row['config']}{' +perturbed' if row['perturbed'] else ''} "
                   f"| {row['trial']} | {score} | "
                   f"{row['checks'] or '-'} | {row['messages']} | {row['duration_s']} | "
                   f"{row['stop_reason']} |")

    flagged = [row for row in rows if row["flags"]]
    if flagged:
        out += ["", "## Flags", ""]
        for row in flagged:
            out.append(f"- **{row['task']}/{row['config']}/trial-{row['trial']}**: "
                       + "; ".join(row["flags"]))

    out += ["", "## Reading these numbers", "",
            "- Token counts are estimates from the prompt sizes in the container logs; the",
            "  provider's real usage never reaches the log. They compare configurations, they",
            "  are not a bill.",
            "- The framed and plain configurations differ only by the context frame, so a",
            "  single trial of each says nothing; the source protocol asks for three.",
            "- A retention verdict of `recalibrate` means the task no longer separates one",
            "  agent from three, not that the run failed."]
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--out", type=Path, default=None,
                        help="markdown destination (default: <runs>/report.md)")
    parser.add_argument("--json", type=Path, default=None,
                        help="also write a machine-readable envelope here")
    args = parser.parse_args()

    trials = load(args.runs)
    if not trials:
        raise SystemExit(f"no runs found under {args.runs}")
    rows = [summarise(trial) for trial in trials]
    medians = by_config(rows)

    destination = args.out or args.runs / "report.md"
    destination.write_text(markdown(rows, medians))
    print(f"{len(rows)} trial(s) -> {destination}")

    if args.json:
        args.json.write_text(json.dumps({"trials": rows, "by_config": [
            {"task": task, "config": config, **value}
            for (task, config), value in sorted(medians.items())]}, indent=2))
        print(f"envelope -> {args.json}")


if __name__ == "__main__":
    main()
