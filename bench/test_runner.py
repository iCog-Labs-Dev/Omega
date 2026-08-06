"""Host-side checks for trial planning. No Docker, no LLM: `pytest bench/test_runner.py`."""

import runner


TASK = {"id": "qr1", "perturbation": {"puppet": "collaborator_1", "reply": "canned"}}


def plan(config="framed", trial=1, perturb=False):
    return runner.plan_trial(TASK, config, trial, budget=24, perturb=perturb)


def test_the_framed_role_is_the_plain_role_plus_the_frame():
    framed, _ = plan("framed")
    plain, _ = plan("plain")

    assert framed[0]["role"].startswith(plain[0]["role"])
    assert "FRAME" in framed[0]["role"] and "FRAME" not in plain[0]["role"]


def test_the_solo_agent_has_no_collaborators():
    agents, _ = plan("solo")

    assert [a["name"] for a in agents] == ["Main"]
    assert "@" not in agents[0]["role"]


def test_collaborator_names_are_filled_in_everywhere():
    agents, _ = plan()
    names = [a["name"] for a in agents[1:]]

    assert len(names) == 2
    for name in names:
        assert name in agents[0]["role"]
    assert "$" not in agents[0]["role"] and "$" not in agents[1]["role"]


def test_each_collaborator_gets_its_own_result_prefix():
    agents, _ = plan()

    assert "QR1-A-1" in agents[1]["role"]
    assert "QR1-B-1" in agents[2]["role"]


def test_names_are_stable_per_trial_and_differ_between_trials():
    first, _ = plan(trial=1)
    again, _ = plan(trial=1)
    second, _ = plan(trial=2)

    assert [a["name"] for a in first] == [a["name"] for a in again]
    assert [a["name"] for a in first] != [a["name"] for a in second]


def test_the_puppet_is_only_resolved_under_perturbation():
    _, without = plan(perturb=False)
    agents, with_puppet = plan(perturb=True)

    assert without is None
    assert with_puppet == agents[1]["name"]
    assert [a["puppet"] for a in agents] == [False, True, False]


def test_only_the_main_agents_final_answer_counts(tmp_path):
    channel = runner.bus.Bus(tmp_path / "t.jsonl")
    channel.say("Agent-A", "FINAL ANSWER 42")       # a collaborator overstepping
    channel.say("Main", "working on it")
    assert runner.final_answer(channel) is None

    channel.say("Main", "FINAL ANSWER the portfolio is A, B, E")
    assert runner.final_answer(channel)["agent"] == "Main"


def test_the_final_answer_includes_its_continuation(tmp_path):
    channel = runner.bus.Bus(tmp_path / "t.jsonl")
    channel.say("Main", "@Agent-A do the sums")
    channel.say("Main", "FINAL ANSWER part one")
    channel.say("Main", "FINAL ANSWER (cont.) part two")

    text = runner.final_answer_text(channel)
    assert "part one" in text and "part two" in text
    assert "do the sums" not in text


def test_no_final_answer_reads_as_none(tmp_path):
    channel = runner.bus.Bus(tmp_path / "t.jsonl")
    channel.say("Main", "still working")

    assert runner.final_answer_text(channel) is None
