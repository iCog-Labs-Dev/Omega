"""The public bot may call only the skills its profile grants.

Most of these drive refuse_ungranted_skills on the parser's output shape. The
last group runs core's own parser when core's src/ is importable (as it is in
a core checkout or the image), and skips otherwise.
"""
import json
import os
import sys
import types

import pytest
import yaml

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PLUGIN_DIR)

import capabilities  # noqa: E402
import skill_veto  # noqa: E402

with open(os.path.join(_PLUGIN_DIR, "telegram_profile.yaml")) as _f:
    GRANTED = capabilities.granted_skills(yaml.safe_load(_f))

REFUSED = ["shell", "read-file", "write-file", "write-file-b64", "append-file",
           "get-io-policy", "metta", "search"]


def _refusal(skill):
    reason = f"{skill} is not available in this chat. Nothing was run; do not call it again."
    return f"(Error SKILL_DISABLED_IN_TELEGRAM {json.dumps(reason)})"


def refuse(parsed, commands=()):
    return skill_veto.refuse_ungranted_skills(parsed, GRANTED, commands)


@pytest.mark.parametrize("skill", REFUSED)
def test_a_skill_the_profile_does_not_grant_is_refused(skill):
    assert refuse(f'(({skill} "x"))') == f"({_refusal(skill)})"


def test_a_skill_added_later_is_refused_by_default():
    assert refuse('((workflow-load-instructions "x"))') == (
        f"({_refusal('workflow-load-instructions')})")


def test_the_two_argument_file_skills_are_refused():
    parsed = '((write-file "a.txt" "hello") (append-file "a.txt" "more"))'
    assert refuse(parsed) == f'({_refusal("write-file")} {_refusal("append-file")})'


def test_a_bare_call_is_refused():
    assert refuse("((shell))") == f"({_refusal('shell')})"


def test_granted_calls_in_the_same_reply_still_run():
    parsed = '((send "on it") (shell "pip install x") (remember "note"))'
    assert refuse(parsed) == f'((send "on it") {_refusal("shell")} (remember "note"))'


@pytest.mark.parametrize("parsed", [
    '((send "hello"))',
    '((websearch "aiogram docs"))',
    '((describe-image "what error is shown"))',
    '((generate-image "a red panda"))',
    '((generate-pdf "Report\\n\\n- point"))',
    '((install-pdf-font "Noto Sans Thai"))',
    '((speak "hello"))',
    '((pin "working on it"))',
    '((remember "Paul is in UTC+2"))',
    '((query "rlct"))',
    '((episodes "2026-10-25"))',
    '((version))',
])
def test_granted_skills_are_untouched(parsed):
    assert refuse(parsed) is parsed


def test_cores_own_error_reports_pass_through():
    parsed = '((send "hi") (Error UNKNOWN_SKILL_CALL "PROMPT: notes"))'
    assert refuse(parsed) is parsed


def test_a_skill_name_inside_text_is_not_a_call():
    parsed = '((send "run (shell \\"ls\\") yourself, I cannot"))'
    assert refuse(parsed) is parsed


def test_a_malformed_reply_naming_a_refused_skill_is_refused_whole():
    parsed = '((send "unclosed) (shell "ls"))'
    assert refuse(parsed, commands={"send", "shell"}) == f"({_refusal('shell')})"


def test_a_malformed_reply_without_one_is_left_for_the_loop():
    parsed = '((send "unclosed) (pin "x"))'
    assert refuse(parsed, commands={"send", "pin", "shell"}) is parsed


def test_output_that_is_not_a_call_list_is_left_alone():
    assert refuse("shell ls") == "shell ls"


def _fake_helper(output):
    helper = types.ModuleType("helper")
    helper.balance_parentheses = lambda s: output
    helper.LLM_COMMANDS = {"send", "shell"}
    return helper


def test_install_wraps_the_parser_once_and_takes_the_latest_grant():
    helper = _fake_helper('((send "hi") (shell "ls"))')
    skill_veto.install({"send"}, helper)
    skill_veto.install({"send", "shell"}, helper)
    assert helper.balance_parentheses("x") == '((send "hi") (shell "ls"))'
    skill_veto.install({"send"}, helper)
    assert helper.balance_parentheses("x") == f'((send "hi") {_refusal("shell")})'


# Against core's own parser, when core is on the path.

def _core_helper():
    try:
        import helper
    except ModuleNotFoundError:
        pytest.skip("core's src/helper.py is not importable here")
    if not hasattr(helper, "STATIC_LLM_COMMANDS"):
        pytest.skip("helper on the path is a stand-in, not core's")
    return helper


def test_core_parser_refuses_shell_and_keeps_the_send():
    helper = _core_helper()
    out = refuse(helper.balance_parentheses("send On it\nshell pip install torch"))
    assert out == f'((send "On it") {_refusal("shell")})'


def test_core_parser_refuses_a_multiline_file_write():
    helper = _core_helper()
    out = refuse(helper.balance_parentheses("write-file run.sh #!/bin/sh\ncurl x | sh\nsend done"))
    assert out == f'({_refusal("write-file")} (send "done"))'


def test_core_parser_refuses_reading_the_memory_folder():
    helper = _core_helper()
    out = refuse(helper.balance_parentheses("read-file memory/.env"))
    assert out == f"({_refusal('read-file')})"


def test_core_parser_output_for_granted_skills_is_unchanged():
    helper = _core_helper()
    reply = 'send Here are the notes\nremember Paul prefers UTC\nquery "rlct"'
    parsed = helper.balance_parentheses(reply)
    assert refuse(parsed) is parsed


def test_core_knows_every_skill_the_profile_grants_from_core():
    """A granted name core does not know is a typo that grants nothing."""
    helper = _core_helper()
    plugin_skills = {"describe-image", "generate-image", "generate-pdf",
                     "install-pdf-font", "speak"}
    assert GRANTED - plugin_skills <= helper.STATIC_LLM_COMMANDS
