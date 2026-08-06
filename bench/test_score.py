"""Checks for the deterministic scorer: `pytest bench/test_score.py`.

Two fixture trials carry most of the weight: one that does everything right, and one that
copies the prompt to both collaborators, finalizes before anyone answers, repeats itself,
and gets the answer wrong. Every metric should be able to tell them apart.
"""

import json

import pytest

import runner
import score

GOOD_ANSWER = """FINAL ANSWER
1. Value-maximizing portfolio: A, B, E.
2. Cost 89, value 154 (142 base plus synergies 8 and 4).
3. Top three: A, B, E at 154; A, B, D at 147; B, D, E at 134.
4. Margin best minus second: 7.
5. With costs 10% higher, A, B, E costs 97.9 and A, B, D costs 92.4, both infeasible.
   The new optimum is B, D, E, value 134.
"""


def build_trial(tmp_path, messages, answer, agents, task="qr1", config="framed",
                stop_reason="final_answer", history="", logs=None):
    trial = tmp_path / task / config / "trial-1"
    for name in agents:
        memory = trial / "agents" / name / "memory"
        memory.mkdir(parents=True)
        (memory / "history.metta").write_text(history if name == runner.MAIN_NAME else "")
        (trial / "agents" / name / "docker.log").write_text((logs or {}).get(name, ""))
    numbered = [{"seq": index, "ts": 0.0, "agent": agent, "text": text}
                for index, (agent, text) in enumerate(messages, start=1)]
    (trial / "run.json").write_text(json.dumps({
        "task": task, "config": config, "trial": 1, "stop_reason": stop_reason,
        "duration_s": 100.0,
        "agents": [{"name": name, "puppet": False} for name in agents],
        "final_answer": answer, "messages": numbered, "message_count": len(numbered),
    }))
    return trial


@pytest.fixture
def clean(tmp_path):
    trial = build_trial(
        tmp_path,
        [("User", "@Main solve the portfolio problem"),
         ("Main", "@Agent-A enumerate every feasible portfolio under budget and count "
                  "constraints, and value each candidate including cumulative synergy"),
         ("Main", "@Wren re-optimize after the ten percent cost increase and verify the "
                  "ranking and the margin independently"),
         ("Agent-A", "[RESULT QR1-A-1] Finding: A, B, E at 154. Evidence: cost 89."),
         ("Wren", "[RESULT QR1-B-1] Finding: B, D, E at 134 once costs rise. "
                  "Agent-A's enumeration is confirmed."),
         ("Main", GOOD_ANSWER)],
        GOOD_ANSWER, [runner.MAIN_NAME, "Agent-A", "Wren"],
        history="pin FRAME goal: portfolio subgoals: done constraints: budget 90 "
                "hypotheses: ABE results: QR1-A-1 rejected: none budget: 6/24 "
                "deliverables: done state: done",
        logs={runner.MAIN_NAME: "CHARS_SENT: 4000\nCHARS_SENT: 4400\n"})
    return score.score_trial(trial)


@pytest.fixture
def sloppy(tmp_path):
    task = runner.load_task("qr1")
    trial = build_trial(
        tmp_path,
        [("User", f"@Main {task['prompt']}"),
         ("Main", f"@Agent-A {task['prompt']}"),
         ("Main", f"@Wren {task['prompt']}"),
         ("Main", "FINAL ANSWER We could not determine the portfolio."),
         ("Main", "FINAL ANSWER We could not determine the portfolio."),
         ("Agent-A", "@Wren do you agree with me?")],
        "FINAL ANSWER We could not determine the portfolio.",
        [runner.MAIN_NAME, "Agent-A", "Wren"])
    return score.score_trial(trial)


def test_a_clean_trial_passes_every_task_check(clean):
    assert clean["checks_passed"] == clean["checks_total"]
    assert clean["flags"] == []


def test_a_clean_trial_reads_as_well_organised(clean):
    metrics = clean["metrics"]

    assert metrics["role_distinctness"]["redundant"] is False
    assert metrics["collaborator_utilization"]["ratio"] == 1.0
    assert metrics["context_selectivity"]["copied_wholesale"] is False
    assert metrics["channel_discipline"]["finalized_before_any_result"] is False
    assert metrics["efficiency"]["band"] == "free"
    assert metrics["efficiency"]["estimated_prompt_tokens"] == 2100


def test_a_clean_trial_records_its_frame(clean):
    frame = clean["metrics"]["frame_continuity"]

    assert frame["frames"] == 1
    assert frame["missing_from_last_frame"] == []


def test_the_sloppy_trial_fails_the_task_checks(sloppy):
    assert sloppy["checks_passed"] == 0


def test_the_sloppy_trial_is_caught_on_every_dimension(sloppy):
    metrics = sloppy["metrics"]

    assert metrics["role_distinctness"]["redundant"] is True
    assert metrics["context_selectivity"]["copied_wholesale"] is True
    assert metrics["collaborator_utilization"]["ratio"] == 0.5
    assert metrics["channel_discipline"]["finalized_before_any_result"] is True
    assert len(metrics["channel_discipline"]["final_answer_messages"]) == 2
    assert metrics["channel_discipline"]["near_identical_pairs"]
    assert metrics["channel_discipline"]["collaborator_to_collaborator"] == [6]
    assert metrics["channel_discipline"]["unprompted_collaborator_messages"] == []


def test_the_sloppy_trial_flags_read_like_a_summary(sloppy):
    joined = " | ".join(sloppy["flags"])

    for expected in ["failed checks", "finalized before", "FINAL ANSWER",
                     "near-identical", "similar", "wholesale", "never contributed"]:
        assert expected in joined


def test_a_missing_answer_fails_rather_than_crashes(tmp_path):
    trial = build_trial(tmp_path, [("User", "@Main go")], None,
                        [runner.MAIN_NAME, "Agent-A", "Wren"], stop_reason="wall_clock")

    result = score.score_trial(trial)
    assert result["checks_passed"] == 0
    assert "stopped on wall_clock" in result["flags"]


def test_a_single_agent_trial_is_not_penalised_for_being_quiet(tmp_path):
    trial = build_trial(tmp_path, [("User", "@Main go"), ("Main", GOOD_ANSWER)],
                        GOOD_ANSWER, [runner.MAIN_NAME], config="solo")

    result = score.score_trial(trial)
    assert result["metrics"]["efficiency"]["band"] == "single agent"
    assert result["metrics"]["channel_discipline"]["finalized_before_any_result"] is False
    assert result["flags"] == []


def test_percentages_satisfy_a_probability_check():
    check = {"kind": "number", "label": "posterior", "expect": 0.8805, "tolerance": 0.002}

    assert score._check_number(check, "the posterior is 88.05%")[0]
    assert score._check_number(check, "the posterior is 0.8805")[0]
    assert not score._check_number(check, "the posterior is 0.42")[0]


def test_a_word_limit_is_counted_inside_its_marker():
    check = {"kind": "words", "label": "plaque", "region": "PLAQUE", "between": [1, 5]}

    assert score._check_words(check, "<PLAQUE>one two three</PLAQUE>")[0]
    assert not score._check_words(check, "<PLAQUE>one two three four five six</PLAQUE>")[0]
    assert not score._check_words(check, "no marker at all")[0]
