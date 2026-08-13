"""Score a finished trial from its transcript, without asking a model anything.

Everything here is arithmetic and text matching: did the final answer contain the values
the evaluator key says it should, and what do the messages say about how the work was
organised. The judgement calls the source rubric asks for — was the decomposition sensible,
were collaborator findings restated faithfully — are deliberately left out, and the numbers
this produces are handed to the judge so it cannot contradict them.

    bench/score.py bench/runs/qr1/framed/trial-1
    bench/score.py --all

Writes `score.json` beside each `run.json` and prints one line per trial.
"""

import argparse
import difflib
import itertools
import json
import re
from pathlib import Path

import runner

HERE = Path(__file__).resolve().parent

# Metric 7.2: two delegations more similar than this are probably the same request twice.
ROLE_DISTINCTNESS_FLAG = 0.70
# Metric 7.7: messages this similar from one author are a repeat, not a new contribution.
REPEAT_FLAG = 0.90
# Section 7.9 message bands: free, small penalty, then inspect for looping.
MESSAGE_BANDS = [(6, 14, "free"), (15, 20, "small penalty"), (21, 10 ** 6, "inspect")]

CONTRADICTION_WORDS = ["contradict", "disagree", "incorrect", "error", "infeasible",
                       "correcting", "correction", "wrong", "mistake", "does not match"]
CLOSURE_WORDS = ["confirm", "agree", "verified", "corrected", "re-checked", "rechecked",
                 "audit"]


# --- task checks -----------------------------------------------------------------

def _numbers(text):
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]


def _check_number(check, answer):
    """Pass when some number in the answer matches, in plain or percentage form.

    A probability may be written 0.8805 or 88.05%, and a percentage 65 or 0.65, so both
    forms are accepted — each with its own tolerance, scaled the same way as the value.
    """
    expect, tolerance = float(check["expect"]), float(check.get("tolerance", 0.01))
    forms = [(expect, tolerance)]
    forms.append((expect * 100, tolerance * 100) if abs(expect) <= 1
                 else (expect / 100, tolerance / 100))
    for found in _numbers(answer):
        for target, slack in forms:
            if abs(found - target) <= slack:
                return True, f"found {found}"
    return False, f"no number within {tolerance} of {expect}"


# Ways an answer joins project letters: "A+B+E", "A, B, E", "A, B, and E", "A and B".
# The Oxford form comes first so "and" is eaten with its comma rather than read as an item.
_SEPARATOR = r"\s*(?:,\s*and\b|,|\+|&|/|and\b)\s*"
# A lone letter, so the "a" starting "and" and the "t" ending "cost" are not items.
_ITEM = r"\b[A-Za-z]\b"


def _check_set(check, answer):
    """Pass when the answer lists exactly this group somewhere, e.g. {A, B, E} or A+B+E.

    Single-letter groups are also accepted written solid, as `BDE`, which is how answers
    often shorten a portfolio. Before 2026-08-13 only the separated forms counted, and a
    correct answer written that way was scored wrong — and the judge, told the checks are
    authoritative, then deducted correctness for it.

    ponytail: permutations of the expected letters, so `{A, D, E}` also accepts the word
    `DEA`. Six forms for a three-item group and the match is case-sensitive on uppercase,
    which keeps it clear of ordinary prose. Upgrade path is requiring the group to sit
    next to one of the task's own labels for it.
    """
    expect = {str(item).upper() for item in check["expect"]}
    for match in re.finditer(rf"{_ITEM}(?:{_SEPARATOR}{_ITEM})+", answer):
        group = {part.strip().upper() for part in
                 re.split(_SEPARATOR, match.group()) if part.strip()}
        if group == expect:
            return True, f"found {match.group().strip()}"
    if all(len(item) == 1 for item in expect):
        for order in itertools.permutations(sorted(expect)):
            solid = "".join(order)
            if re.search(rf"\b{solid}\b", answer):
                return True, f"found {solid}"
    return False, f"no group equal to {sorted(expect)}"


def _check_phrase(check, answer):
    lowered = answer.lower()
    wanted = check.get("any_of") or [check["expect"]]
    for phrase in wanted:
        if str(phrase).lower() in lowered:
            return True, f"found {phrase!r}"
    return False, f"none of {wanted} present"


def _check_absent(check, answer):
    present = str(check["expect"]).lower() in answer.lower()
    return not present, f"{'present' if present else 'absent'}: {check['expect']!r}"


def _check_words(check, answer):
    """Count words inside the deliverable's marker, e.g. <PLAQUE>...</PLAQUE>."""
    region = check["region"]
    match = re.search(rf"<{region}>(.*?)</{region}>", answer, re.S)
    if not match:
        return False, f"no <{region}> region in the answer"
    count = len(match.group(1).split())
    low, high = check["between"]
    return low <= count <= high, f"{count} words, wanted {low}-{high}"


def _check_regex(check, answer):
    """Pass when the pattern matches, case-insensitively and across line breaks.

    For a fact whose wording varies but whose shape does not. `phrase` needs the literal
    text, which fails when an answer states the fact correctly in words nobody listed —
    "choose B" against a check written for "Model B". A pattern says what is required and
    leaves the phrasing free:

        kind: regex
        expect: 'RECOMMENDATION:[^.]{0,120}?\\bB\\b'

    Prefer `phrase` when a fixed string really is required. Reach for this when the fact is
    a labelled item, a time span, or anything an answer can shorten.
    """
    found = re.search(check["expect"], answer, re.IGNORECASE | re.DOTALL)
    return ((True, f"matched {found.group()[:70].strip()!r}") if found
            else (False, f"no match for {check['expect']}"))


CHECKERS = {"number": _check_number, "set": _check_set, "phrase": _check_phrase,
            "absent": _check_absent, "words": _check_words, "regex": _check_regex}


def run_checks(task, answer):
    if answer is None:
        return [{**check, "passed": False, "detail": "no final answer was published"}
                for check in task["checks"]]
    results = []
    for check in task["checks"]:
        passed, detail = CHECKERS[check["kind"]](check, answer)
        results.append({"kind": check["kind"], "label": check["label"],
                        "passed": passed, "detail": detail})
    return results


# --- orchestration metrics -------------------------------------------------------

def _addressed(text):
    """Names this message addresses, as written with an at sign."""
    return {name.lower() for name in re.findall(r"@([A-Za-z][\w-]*)", text)}


def _similarity(left, right):
    return round(difflib.SequenceMatcher(None, left, right).ratio(), 3)


def _content_words(text):
    return {word for word in re.findall(r"[a-z]{4,}", text.lower())}


def delegation_coverage(task, delegations, main_messages):
    """Metric 7.1. How many required work packages show up in what the main agent said.

    ponytail: keyword overlap, not meaning. A package counts as covered when half its
    distinctive words appear in one message. The judge confirms; upgrade to embeddings if
    this proves too blunt.
    """
    covered, detail = 0, []
    for package in task["work_packages"]:
        wanted = _content_words(package)
        hit = any(len(wanted & _content_words(message)) >= max(2, len(wanted) // 2)
                  for message in delegations + main_messages)
        covered += hit
        detail.append({"package": package, "covered": hit})
    total = len(task["work_packages"])
    return {"covered": covered, "total": total,
            "ratio": round(covered / total, 3) if total else None, "packages": detail}


def role_distinctness(delegations_by_agent):
    """Metric 7.2. Similarity of the first delegation sent to each collaborator."""
    firsts = [messages[0] for messages in delegations_by_agent.values() if messages]
    if len(firsts) < 2:
        return {"similarity": None, "redundant": None,
                "note": "fewer than two collaborators were briefed"}
    similarity = _similarity(firsts[0], firsts[1])
    return {"similarity": similarity, "redundant": similarity > ROLE_DISTINCTNESS_FLAG}


def context_selectivity(task, delegations):
    """Metric 7.6. How much of each delegation is just the task prompt copied over."""
    ratios = [_similarity(task["prompt"], message) for message in delegations]
    return {"prompt_similarity": ratios,
            "worst": max(ratios) if ratios else None,
            "copied_wholesale": any(ratio > ROLE_DISTINCTNESS_FLAG for ratio in ratios)}


def critique_closure(messages):
    """Metric 7.5. Concerns raised, and whether anything later addressed them."""
    raised = [m["seq"] for m in messages
              if any(word in m["text"].lower() for word in CONTRADICTION_WORDS)]
    closed = [seq for seq in raised
              if any(m["seq"] > seq and any(word in m["text"].lower() for word in CLOSURE_WORDS)
                     for m in messages)]
    return {"raised": len(raised), "closed": len(closed),
            "ratio": round(len(closed) / len(raised), 3) if raised else None,
            "raised_at": raised}


def channel_discipline(messages, collaborators, main=runner.MAIN_NAME, puppets=()):
    """Metric 7.7. Every way the channel can be abused, counted.

    A puppet is scripted by the harness and answers with the same fixed text every time it
    is addressed, so its repeats are the fixture talking, not an agent misbehaving. They
    are excluded from the repeat count; everything a real agent does still counts.
    """
    finals = [m for m in messages
              if m["agent"] == main and m["text"].strip().upper().startswith("FINAL ANSWER")]
    results = [m for m in messages if m["agent"] in collaborators]

    unprompted = []
    for message in results:
        name = message["agent"].lower()
        earlier = [m for m in messages if m["seq"] < message["seq"]]
        last_own = max([m["seq"] for m in earlier if m["agent"] == message["agent"]], default=0)
        if not any(name in _addressed(m["text"])
                   for m in earlier if m["seq"] > last_own):
            unprompted.append(message["seq"])

    real = [m for m in messages if m["agent"] not in puppets]
    repeats = []
    for index, first in enumerate(real):
        for second in real[index + 1:]:
            if first["agent"] == second["agent"] and \
                    _similarity(first["text"], second["text"]) >= REPEAT_FLAG:
                repeats.append([first["seq"], second["seq"]])

    cross_talk = [m["seq"] for m in results
                  if _addressed(m["text"]) & {c.lower() for c in collaborators} - {m["agent"].lower()}]

    # With no collaborators there is nothing to wait for, so finalizing early is not a
    # discipline failure — it is the whole single-agent configuration.
    premature = bool(collaborators) and bool(finals) and \
        (not results or finals[0]["seq"] < results[0]["seq"])

    return {"final_answer_messages": [m["seq"] for m in finals],
            "unprompted_collaborator_messages": unprompted,
            "near_identical_pairs": repeats,
            "collaborator_to_collaborator": cross_talk,
            "finalized_before_any_result": premature}


FRAME_SKILLS = ["ctx-add-hypothesis", "ctx-add-result",
                "complete-goals-stm", "complete-goals-ltm"]


def frame_continuity(trial_dir, main=runner.MAIN_NAME):
    """Metric 7.8. How much the main agent drove the runtime's own context frame.

    The frame itself is maintained by the runtime, not written by the agent, so there is
    no frame text to find in history.metta. What is findable is the agent's own calls
    into it: each of FRAME_SKILLS lands in its history the same way any other action does.
    """
    history = trial_dir / "agents" / main / "memory" / "history.metta"
    if not history.exists():
        return {"frame_calls": 0, "note": "no history file"}
    text = history.read_text(errors="ignore")
    calls = {skill: text.count(skill) for skill in FRAME_SKILLS}
    return {"frame_calls": sum(calls.values()), "calls": calls,
            "completed": calls["complete-goals-stm"] > 0 or calls["complete-goals-ltm"] > 0}


USAGE_LINE = re.compile(r"input_tokens=(\d+) output_tokens=(\d+) total_tokens=\d+ "
                        r"cached_tokens=(\d+)")


def usage(trial_dir, agents):
    """Metric 7.9's cost half: real provider usage, per agent and summed.

    The provider layer logs one `input_tokens=... cached_tokens=...` line per call, so the
    billed numbers are recoverable from the container log rather than estimated. Cached
    input is reported separately because it is billed at a fraction of fresh input, and
    because a sweep with caching cannot be compared on cost to one without.
    """
    per_agent, total = {}, {"calls": 0, "input": 0, "cached_input": 0, "output": 0}
    for agent in agents:
        log = trial_dir / "agents" / agent / "docker.log"
        found = (USAGE_LINE.findall(log.read_text(errors="ignore"))
                 if log.exists() else [])
        counts = {"calls": len(found),
                  "input": sum(int(i) for i, _, _ in found),
                  "cached_input": sum(int(c) for _, _, c in found),
                  "output": sum(int(o) for _, o, _ in found)}
        per_agent[agent] = counts
        for key in total:
            total[key] += counts[key]
    total["fresh_input"] = total["input"] - total["cached_input"]
    total["cache_hit_rate"] = (round(total["cached_input"] / total["input"], 3)
                               if total["input"] else None)
    return {"total": total, "per_agent": per_agent}


def efficiency(trial_dir, messages, agents, collaborators):
    """Metric 7.9. Message band, and the prompt bytes the loop pushed through the provider.

    The bands count inter-agent traffic, so they only mean something when there is more
    than one agent; a single agent talking to itself is not spending coordination.

    `estimated_prompt_tokens` is kept only so this sweep stays comparable with the ones
    before 2026-08-13, which had no better number. Use `metrics.usage` for anything real.
    """
    chars = 0
    for agent in agents:
        log = trial_dir / "agents" / agent / "docker.log"
        if log.exists():
            chars += sum(int(n) for n in
                         re.findall(r"CHARS_SENT: (\d+)", log.read_text(errors="ignore")))
    count = len(messages)
    if not collaborators:
        band = "single agent"
    elif count < 6:
        band = "below band"
    else:
        band = next(name for low, high, name in MESSAGE_BANDS if low <= count <= high)
    inter_agent = sum(len(m["text"]) for m in messages if m["agent"] != "User")
    return {"messages": count, "band": band,
            "prompt_chars": chars, "estimated_prompt_tokens": chars // 4,
            "inter_agent_chars": inter_agent}


# --- putting it together ---------------------------------------------------------

def score_trial(trial_dir):
    trial_dir = Path(trial_dir)
    run = json.loads((trial_dir / "run.json").read_text())
    task = runner.load_task(run["task"])
    answer = run.get("final_answer")

    collaborators = [a["name"] for a in run["agents"] if a["name"] != runner.MAIN_NAME]
    messages = run["messages"]
    main_texts = [m["text"] for m in messages if m["agent"] == runner.MAIN_NAME]
    delegations_by_agent = {name: [m["text"] for m in messages
                                  if m["agent"] == runner.MAIN_NAME
                                  and name.lower() in _addressed(m["text"])]
                           for name in collaborators}
    delegations = [text for texts in delegations_by_agent.values() for text in texts]

    checks = run_checks(task, answer)
    contributed = [name for name in collaborators
                   if any(m["agent"] == name for m in messages)]

    score = {
        "task": run["task"], "config": run["config"], "trial": run["trial"],
        "stop_reason": run["stop_reason"], "duration_s": run["duration_s"],
        "perturbed": bool(run.get("puppet_reply")),
        "checks": checks,
        "checks_passed": sum(c["passed"] for c in checks),
        "checks_total": len(checks),
        "metrics": {
            "delegation_coverage": delegation_coverage(task, delegations, main_texts),
            "role_distinctness": role_distinctness(delegations_by_agent),
            "collaborator_utilization": {
                "contributed": contributed,
                "of": len(collaborators),
                "ratio": round(len(contributed) / len(collaborators), 3) if collaborators else None},
            "context_selectivity": context_selectivity(task, delegations),
            "critique_closure": critique_closure(messages),
            "channel_discipline": channel_discipline(
                messages, collaborators,
                puppets={a["name"] for a in run["agents"] if a["puppet"]}),
            "frame_continuity": frame_continuity(trial_dir),
            "efficiency": efficiency(trial_dir, messages,
                                     [a["name"] for a in run["agents"] if not a["puppet"]],
                                     collaborators),
            "usage": usage(trial_dir,
                           [a["name"] for a in run["agents"] if not a["puppet"]]),
        },
    }
    score["flags"] = flags(score)
    (trial_dir / "score.json").write_text(json.dumps(score, indent=2))
    return score


def flags(score):
    """The short list a human should look at before reading anything else."""
    out = []
    metrics, discipline = score["metrics"], score["metrics"]["channel_discipline"]
    if score["checks_passed"] < score["checks_total"]:
        failed = [c["label"] for c in score["checks"] if not c["passed"]]
        out.append(f"failed checks: {', '.join(failed)}")
    if score["stop_reason"] != "final_answer":
        out.append(f"stopped on {score['stop_reason']}")
    if discipline["finalized_before_any_result"]:
        out.append("finalized before any collaborator result arrived")
    if len(discipline["final_answer_messages"]) > 1:
        out.append(f"{len(discipline['final_answer_messages'])} messages open with FINAL ANSWER")
    if discipline["near_identical_pairs"]:
        out.append(f"{len(discipline['near_identical_pairs'])} near-identical message pairs")
    if discipline["collaborator_to_collaborator"]:
        out.append("collaborators addressed each other")
    if metrics["role_distinctness"].get("redundant"):
        out.append(f"delegations {metrics['role_distinctness']['similarity']} similar")
    if metrics["context_selectivity"]["copied_wholesale"]:
        out.append("a delegation copies the task prompt wholesale")
    if metrics["collaborator_utilization"]["ratio"] not in (None, 1.0):
        out.append("a collaborator never contributed")
    if metrics["efficiency"]["band"] not in ("free", "single agent", None):
        out.append(f"message count {metrics['efficiency']['messages']} ({metrics['efficiency']['band']})")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trials", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="score every run under bench/runs")
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    args = parser.parse_args()

    trials = args.trials
    if args.all or not trials:
        trials = sorted(p.parent for p in args.runs.rglob("run.json"))

    for trial in trials:
        score = score_trial(trial)
        head = f"{score['task']}/{score['config']}/trial-{score['trial']}"
        used = score["metrics"]["usage"]["total"]
        print(f"{head}: checks {score['checks_passed']}/{score['checks_total']}, "
              f"{score['metrics']['efficiency']['messages']} messages, "
              f"{used['calls']} calls, {used['input']:,} in "
              f"({used['cached_input']:,} cached), {used['output']:,} out")
        for flag in score["flags"]:
            print(f"    - {flag}")


if __name__ == "__main__":
    main()
