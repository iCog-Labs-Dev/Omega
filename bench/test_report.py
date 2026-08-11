"""Checks for aggregation and the retention verdict: `pytest bench/test_report.py`."""

import json

import report
import runner
import score as scorer
from test_score import GOOD_ANSWER, build_trial


def scored_trial(tmp_path, task, config, trial_no, answer, judge_total=None,
                 puppet_reply=None, perturbation_points=None):
    """A trial on disk, optionally with a judge verdict already attached."""
    trial = build_trial(tmp_path, [("User", "@Main go"), ("Main", answer)], answer,
                        [runner.MAIN_NAME] if config == "solo"
                        else [runner.MAIN_NAME, "Agent-A", "Wren"],
                        task=task, config=config)
    run = json.loads((trial / "run.json").read_text())
    run["trial"] = trial_no
    if puppet_reply:
        run["puppet_reply"] = puppet_reply
    (trial / "run.json").write_text(json.dumps(run))
    scorer.score_trial(trial)
    if judge_total is not None:
        (trial / "judge.json").write_text(json.dumps({
            "task": task, "config": config, "trial": trial_no,
            "rubric_total": judge_total, "rubric_max": 100,
            "perturbation_points": perturbation_points, "scores": {}, "missing_lines": [],
            "summary": "fixture"}))
    return trial


def test_trials_without_a_judge_verdict_still_aggregate(tmp_path):
    scored_trial(tmp_path, "qr1", "framed", 1, GOOD_ANSWER)

    rows = [report.summarise(t) for t in report.load(tmp_path)]
    assert rows[0]["judge_total"] is None
    assert "unjudged" in report.markdown(rows, report.by_config(rows))


def test_medians_come_from_the_trials_of_one_config(tmp_path):
    for index, total in enumerate([70, 90, 80], start=1):
        scored_trial(tmp_path / f"t{index}", "qr1", "framed", index, GOOD_ANSWER, total)

    rows = [report.summarise(t) for t in report.load(tmp_path)]
    medians = report.by_config(rows)
    assert medians[("qr1", "framed")]["trials"] == 3
    assert medians[("qr1", "framed")]["judge_median"] == 80


def test_a_task_that_separates_the_configs_is_retained(tmp_path):
    scored_trial(tmp_path / "a", "qr1", "solo", 1, GOOD_ANSWER, 60)
    scored_trial(tmp_path / "b", "qr1", "framed", 1, GOOD_ANSWER, 88)

    medians = report.by_config([report.summarise(t) for t in report.load(tmp_path)])
    verdict, delta = report.retention("qr1", medians)
    assert verdict == "retain"
    assert delta == 28


def test_a_task_the_single_agent_aces_is_flagged(tmp_path):
    scored_trial(tmp_path / "a", "qr1", "solo", 1, GOOD_ANSWER, 92)
    scored_trial(tmp_path / "b", "qr1", "framed", 1, GOOD_ANSWER, 94)

    medians = report.by_config([report.summarise(t) for t in report.load(tmp_path)])
    verdict, _ = report.retention("qr1", medians)
    assert verdict.startswith("recalibrate")
    assert "single agent" in verdict


def test_a_task_where_orchestration_underperforms_is_flagged(tmp_path):
    scored_trial(tmp_path / "a", "qr1", "solo", 1, GOOD_ANSWER, 60)
    scored_trial(tmp_path / "b", "qr1", "framed", 1, GOOD_ANSWER, 70)

    medians = report.by_config([report.summarise(t) for t in report.load(tmp_path)])
    verdict, delta = report.retention("qr1", medians)
    assert verdict.startswith("recalibrate")
    assert "orchestrated" in verdict
    assert delta == 10


def test_a_missing_baseline_is_reported_as_missing_not_as_a_pass(tmp_path):
    scored_trial(tmp_path, "qr1", "framed", 1, GOOD_ANSWER, 88)

    medians = report.by_config([report.summarise(t) for t in report.load(tmp_path)])
    verdict, delta = report.retention("qr1", medians)
    assert verdict == "not enough data"
    assert delta is None


def test_the_report_states_what_the_numbers_do_not_mean(tmp_path):
    scored_trial(tmp_path / "a", "qr1", "solo", 1, GOOD_ANSWER, 60)
    scored_trial(tmp_path / "b", "qr1", "framed", 1, GOOD_ANSWER, 88)

    rows = [report.summarise(t) for t in report.load(tmp_path)]
    text = report.markdown(rows, report.by_config(rows))
    assert "estimates" in text                       # tokens are not a bill
    assert "three" in text                           # one trial proves nothing
    assert "| qr1 | solo | 1 |" in text


def test_a_perturbed_trial_is_excluded_from_the_config_median(tmp_path):
    scored_trial(tmp_path / "a", "qr1", "framed", 1, GOOD_ANSWER, 80)
    scored_trial(tmp_path / "b", "qr1", "framed", 2, GOOD_ANSWER, 90)
    scored_trial(tmp_path / "c", "qr1", "framed", 3, GOOD_ANSWER, 40,
                puppet_reply="A+B+D is optimal with value 147.", perturbation_points=2)

    rows = [report.summarise(t) for t in report.load(tmp_path)]
    medians = report.by_config(rows)
    assert medians[("qr1", "framed")]["trials"] == 2
    assert medians[("qr1", "framed")]["judge_median"] == 85


def test_perturbation_runs_get_their_own_section(tmp_path):
    scored_trial(tmp_path / "a", "qr1", "framed", 1, GOOD_ANSWER, 80)
    scored_trial(tmp_path / "b", "qr1", "framed", 2, GOOD_ANSWER, 89,
                puppet_reply="A+B+D is optimal with value 147.", perturbation_points=6)

    rows = [report.summarise(t) for t in report.load(tmp_path)]
    text = report.markdown(rows, report.by_config(rows))
    assert "Perturbation runs" in text
    assert "excluded from the medians" in text.lower()
    assert "| qr1 | framed | 89 | 6 | yes |" in text
