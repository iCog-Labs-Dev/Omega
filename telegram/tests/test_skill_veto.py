"""The public bot must not reach the shell, the filesystem or MeTTa.

Most of these drive refuse_disabled_skills on the parser's output shape. The
last group runs core's own parser when core's src/ is importable (as it is in
a core checkout or the image), and skips otherwise.
"""
import json
import os
import sys
import types

import pytest

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PLUGIN_DIR)

import skill_veto  # noqa: E402

DISABLED = sorted(skill_veto.DISABLED_SKILLS)


def _refusal(skill):
    reason = f"{skill} is not available in this chat. Nothing was run; do not call it again."
    return f"(Error SKILL_DISABLED_IN_TELEGRAM {json.dumps(reason)})"


@pytest.mark.parametrize("skill", DISABLED)
def test_a_disabled_skill_is_refused(skill):
    assert skill_veto.refuse_disabled_skills(f'(({skill} "x"))') == f"({_refusal(skill)})"


def test_the_two_argument_file_skills_are_refused():
    parsed = '((write-file "a.txt" "hello") (append-file "a.txt" "more"))'
    assert skill_veto.refuse_disabled_skills(parsed) == (
        f'({_refusal("write-file")} {_refusal("append-file")})')


def test_a_bare_call_is_refused():
    assert skill_veto.refuse_disabled_skills("((shell))") == f"({_refusal('shell')})"


def test_other_calls_in_the_same_reply_still_run():
    parsed = '((send "on it") (shell "pip install x") (remember "note"))'
    assert skill_veto.refuse_disabled_skills(parsed) == (
        f'((send "on it") {_refusal("shell")} (remember "note"))')


@pytest.mark.parametrize("parsed", [
    '((read-file "notes.txt"))',
    '((query "rlct"))',
    '((remember "Paul is in UTC+2"))',
    '((pin "working on it"))',
    '((send "hello"))',
    '((websearch "aiogram docs"))',
    '((episodes "2026-10-25"))',
    '((describe-image "what error is shown"))',
    '((generate-pdf "Report\\n\\n- point"))',
])
def test_remaining_skills_are_untouched(parsed):
    assert skill_veto.refuse_disabled_skills(parsed) is parsed


def test_a_skill_name_inside_text_is_not_a_call():
    parsed = '((send "run (shell \\"ls\\") yourself, I cannot"))'
    assert skill_veto.refuse_disabled_skills(parsed) is parsed


def test_a_name_that_only_starts_like_a_skill_is_not_refused():
    parsed = '((shellfish "crab"))'
    assert skill_veto.refuse_disabled_skills(parsed) is parsed


def test_a_malformed_reply_naming_a_disabled_skill_is_refused_whole():
    parsed = '((send "unclosed) (shell "ls"))'
    assert skill_veto.refuse_disabled_skills(parsed) == f"({_refusal('shell')})"


def test_a_malformed_reply_without_one_is_left_for_the_loop():
    parsed = '((send "unclosed) (pin "x"))'
    assert skill_veto.refuse_disabled_skills(parsed) is parsed


def test_output_that_is_not_a_call_list_is_left_alone():
    assert skill_veto.refuse_disabled_skills("shell ls") == "shell ls"


def _fake_helper(output):
    helper = types.ModuleType("helper")
    helper.balance_parentheses = lambda s: output
    return helper


def test_install_wraps_the_parser_once():
    helper = _fake_helper('((shell "ls"))')
    skill_veto.install(helper)
    first = helper.balance_parentheses
    skill_veto.install(helper)
    assert helper.balance_parentheses is first
    assert helper.balance_parentheses("shell ls") == f"({_refusal('shell')})"


def test_starting_the_telegram_channel_installs_the_veto(monkeypatch):
    import test_telegram  # noqa: F401  # stubs core's modules when run standalone
    import telegram_channel as tm

    helper = _fake_helper('((shell "ls"))')
    monkeypatch.setitem(sys.modules, "helper", helper)
    monkeypatch.setattr(tm, "start_telegram", lambda token, chat_id: None)
    monkeypatch.setattr(tm.auth, "get_proxy_url", lambda: "")
    monkeypatch.setenv("TG_BOT_TOKEN", "placeholder")

    tm.TelegramChannel().start()

    assert helper.balance_parentheses("shell ls") == f"({_refusal('shell')})"


def test_loading_the_plugin_without_starting_the_channel_changes_nothing(monkeypatch):
    """Another channel may be the active one; only Telegram's start installs it."""
    import test_telegram  # noqa: F401
    import telegram_channel as tm

    helper = _fake_helper('((shell "ls"))')
    monkeypatch.setitem(sys.modules, "helper", helper)
    monkeypatch.setattr(tm.media_handler, "register_channel", lambda *a: None)
    monkeypatch.setattr(tm.channels, "registerCommChannel", lambda name, ch: None)

    tm.loadOmegaPlugin()

    assert helper.balance_parentheses("shell ls") == '((shell "ls"))'


@pytest.mark.parametrize("skill", DISABLED)
def test_the_channel_reports_the_skill_disabled(skill):
    import test_telegram  # noqa: F401
    import telegram_channel as tm

    assert tm.TelegramChannel().is_tool_disabled(skill) is True


def test_the_channel_still_allows_send():
    import test_telegram  # noqa: F401
    import telegram_channel as tm

    assert tm.TelegramChannel().is_tool_disabled("send") is False


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
    out = skill_veto.refuse_disabled_skills(
        helper.balance_parentheses("send On it\nshell pip install torch"))
    assert out == f'((send "On it") {_refusal("shell")})'


def test_core_parser_refuses_a_multiline_file_write():
    helper = _core_helper()
    out = skill_veto.refuse_disabled_skills(
        helper.balance_parentheses("write-file run.sh #!/bin/sh\ncurl x | sh\nsend done"))
    assert out == f'({_refusal("write-file")} (send "done"))'


def test_core_parser_output_for_allowed_skills_is_unchanged():
    helper = _core_helper()
    reply = 'send Here are the notes\nremember Paul prefers UTC\nquery "rlct"'
    parsed = helper.balance_parentheses(reply)
    assert skill_veto.refuse_disabled_skills(parsed) is parsed
