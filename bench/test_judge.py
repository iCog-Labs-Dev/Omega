"""Checks for the judge's prompt and score handling. No API calls: `pytest bench/test_judge.py`."""

import json

import pytest

import judge
import runner
import score as scorer
from test_score import GOOD_ANSWER, build_trial


@pytest.fixture
def scored(tmp_path):
    trial = build_trial(
        tmp_path,
        [("User", "@Main solve the portfolio problem"),
         ("Main", "@Agent-A enumerate the feasible portfolios"),
         ("Main", "@Wren re-optimize after the cost increase"),
         ("Agent-A", "[RESULT QR1-A-1] Finding: A, B, E at 154."),
         ("Wren", "[RESULT QR1-B-1] Finding: B, D, E at 134."),
         ("Main", GOOD_ANSWER)],
        GOOD_ANSWER, [runner.MAIN_NAME, "Agent-A", "Wren"])
    scorer.score_trial(trial)
    return trial


def prompt_for(trial):
    run = json.loads((trial / "run.json").read_text())
    score = json.loads((trial / "score.json").read_text())
    lines = judge.RUBRIC + ([judge.PERTURBATION] if run.get("puppet_reply") else [])
    return judge.build_prompt(runner.load_task(run["task"]), run, score, lines), lines


def test_the_prompt_carries_everything_the_judge_needs(scored):
    prompt, _ = prompt_for(scored)

    for section in ["Task given to the agents", "Required deliverables", "Evaluator key",
                    "Documented failure conditions", "Deterministic check results",
                    "channel transcript", "published final answer", "Rubric"]:
        assert section in prompt


def test_the_prompt_marks_deterministic_results_as_authoritative(scored):
    prompt, _ = prompt_for(scored)

    assert "authoritative" in prompt
    assert "optimal portfolio: PASSED" in prompt
    assert "[RESULT QR1-A-1]" in prompt          # the transcript is verbatim


def test_a_clean_run_prompt_has_no_injected_error_section(scored):
    prompt, lines = prompt_for(scored)

    assert "Injected error" not in prompt
    assert "perturbation" not in [name for name, _, _ in lines]


def test_a_perturbed_run_adds_the_error_and_its_rubric_line(tmp_path):
    trial = build_trial(tmp_path, [("User", "@Main go"), ("Main", GOOD_ANSWER)],
                        GOOD_ANSWER, [runner.MAIN_NAME, "Agent-A", "Wren"])
    run = json.loads((trial / "run.json").read_text())
    run["puppet_reply"] = "[RESULT QR1-A-1] Finding: A+B+D is optimal with value 147."
    (trial / "run.json").write_text(json.dumps(run))
    scorer.score_trial(trial)

    prompt, lines = prompt_for(trial)
    assert "Injected error" in prompt
    assert "147" in prompt and "A+B+E" in prompt      # the error and the expected recovery
    assert "perturbation" in [name for name, _, _ in lines]


def test_the_schema_only_accepts_the_rubric_line_names():
    schema = judge._schema(judge.RUBRIC)
    names = schema["properties"]["scores"]["items"]["properties"]["metric"]["enum"]

    assert names == [name for name, _, _ in judge.RUBRIC]
    assert schema["additionalProperties"] is False


def test_scores_are_clamped_to_their_maximum():
    verdict = {"summary": "ok", "scores": [
        {"metric": "correctness", "points": 99, "justification": "over"},
        {"metric": "uncertainty", "points": -4, "justification": "under"},
    ]}

    result = judge.collect(verdict, judge.RUBRIC)
    assert result["scores"]["correctness"]["points"] == 25
    assert result["scores"]["uncertainty"]["points"] == 0


def test_unscored_lines_are_reported_not_hidden():
    verdict = {"summary": "ok", "scores": [
        {"metric": "correctness", "points": 20, "justification": "mostly right"},
    ]}

    result = judge.collect(verdict, judge.RUBRIC)
    assert result["rubric_total"] == 20
    assert result["rubric_max"] == 100
    assert "uncertainty" in result["missing_lines"]


def test_perturbation_points_stay_out_of_the_hundred():
    lines = judge.RUBRIC + [judge.PERTURBATION]
    verdict = {"summary": "ok", "scores": [
        {"metric": "correctness", "points": 25, "justification": "right"},
        {"metric": "perturbation", "points": 5, "justification": "caught it"},
    ]}

    result = judge.collect(verdict, lines)
    assert result["rubric_total"] == 25
    assert result["rubric_max"] == 100
    assert result["perturbation_points"] == 5


def test_the_rubric_totals_one_hundred():
    assert sum(maximum for _, maximum, _ in judge.RUBRIC) == 100
    assert judge.PERTURBATION[1] == 6
