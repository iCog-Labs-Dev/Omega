"""Checks over the task files themselves: `pytest bench/test_tasks.py`.

These catch transcription drift. A task's evaluator key and its machine-checkable
expectations are written from the same source, so a number that appears in one and not
the other means one of them was mistyped.
"""

import pytest
import yaml

import runner

TASK_FILES = sorted((runner.HERE / "tasks").glob("*.yaml"))
REQUIRED = ["id", "domain", "title", "prompt", "deliverables", "work_packages",
            "checks", "key", "failure_conditions"]
CHECK_KINDS = {"number", "set", "phrase", "absent", "words"}
SLOTS = {"collaborator_1", "collaborator_2"}


def load(path):
    return yaml.safe_load(path.read_text())


@pytest.fixture(params=TASK_FILES, ids=lambda p: p.stem)
def task(request):
    return load(request.param), request.param


def test_the_task_files_are_all_there():
    assert [p.stem for p in TASK_FILES] == ["la1", "la2", "la3", "la4",
                                            "qr1", "qr2", "qr3", "qr4"]


def test_every_task_has_the_required_fields(task):
    data, path = task

    assert data["id"] == path.stem
    for field in REQUIRED:
        assert data.get(field), f"{path.stem} is missing {field}"
    assert data["domain"] in {"liberal-arts", "quantitative"}


def test_every_check_is_well_formed(task):
    data, path = task

    for check in data["checks"]:
        kind, label = check["kind"], check["label"]
        assert kind in CHECK_KINDS, f"{path.stem}/{label}: unknown kind {kind}"
        if kind == "words":
            low, high = check["between"]
            assert check["region"] and 0 < low <= high
        elif kind == "phrase":
            assert check.get("expect") or check.get("any_of")
        else:
            assert "expect" in check


def test_numeric_expectations_appear_in_the_key(task):
    """A number the checks assert but the key never mentions is a typo in one of them."""
    data, path = task

    for check in data["checks"]:
        if check["kind"] != "number":
            continue
        expect = check["expect"]
        written = {str(expect), str(int(expect)) if float(expect).is_integer() else None}
        assert any(form and form in data["key"] for form in written), \
            f"{path.stem}/{check['label']}: {expect} is absent from the key"


def test_set_expectations_appear_in_the_key(task):
    data, path = task

    for check in data["checks"]:
        if check["kind"] != "set":
            continue
        for element in check["expect"]:
            assert element in data["key"], \
                f"{path.stem}/{check['label']}: {element} is absent from the key"


def test_a_perturbation_names_a_real_slot_and_a_recovery(task):
    data, path = task
    perturbation = data.get("perturbation")
    if not perturbation:
        return

    assert perturbation["puppet"] in SLOTS
    assert perturbation["reply"].strip().startswith("[RESULT")
    assert perturbation["expected_recovery"].strip()


def test_the_three_perturbations_the_source_asks_for_exist():
    perturbed = {p.stem for p in TASK_FILES if load(p).get("perturbation")}

    assert perturbed == {"la3", "qr1", "qr4"}


def test_every_task_plans_and_renders(task):
    """A task must survive trial planning: templates filled, no placeholders left."""
    data, _ = task
    agents, _ = runner.plan_trial(data, "framed", 1, budget=24, perturb=False)

    assert len(agents) == 3
    for agent in agents:
        assert "$" not in agent["role"]
