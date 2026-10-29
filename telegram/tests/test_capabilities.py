"""The channel profile says only what the code does, and the code does it.

The profile used to forbid code execution while the bot ran 13,906 shell
commands, because nothing read that section. callable_capabilities is now the
allowlist of skills, and the channel will not start on a profile holding a key
or constraint the plugin does not act on.
"""
import os
import re
import sys
import types

import pytest
import yaml

import test_telegram  # noqa: F401  # stubs core's modules when run standalone
import capabilities
import telegram_channel as tm

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(_PLUGIN_DIR, "telegram_profile.yaml")


def shipped():
    with open(SHIPPED) as f:
        return yaml.safe_load(f)


def test_the_shipped_profile_says_only_what_the_code_does():
    assert capabilities.problems(shipped()) == []


def test_the_shipped_profile_grants_these_skills_and_no_others():
    assert capabilities.granted_skills(shipped()) == {
        "send", "websearch", "describe-image", "generate-image", "generate-pdf",
        "install-pdf-font", "speak", "pin", "remember", "query", "episodes", "version",
    }


def test_the_shipped_profile_keeps_sends_to_the_current_chat():
    assert capabilities.constraints_for(shipped(), "send") == {"same_chat_only"}


def test_the_prompt_names_exactly_the_granted_skills():
    with open(os.path.join(_PLUGIN_DIR, "prompt.txt")) as f:
        prompt = f.read()
    allowed = re.search(r"you may call only (.*?), whatever", prompt, re.S).group(1)
    named = {s.strip() for s in re.split(r",|\band\b", " ".join(allowed.split())) if s.strip()}
    assert named == capabilities.granted_skills(shipped())


def test_without_remember_the_memory_list_may_be_left_out():
    profile = shipped()
    profile["callable_capabilities"]["working_memory"]["skills"] = ["pin", "query", "episodes"]
    profile.pop("internal_learning")
    assert capabilities.problems(profile) == []


def test_a_disabled_capability_grants_nothing():
    profile = shipped()
    profile["callable_capabilities"]["voice_reply"]["enabled"] = False
    assert "speak" not in capabilities.granted_skills(profile)


@pytest.mark.parametrize("edit, expected", [
    (lambda p: p.update(logging={"metadata_events": []}), "logging is not read"),
    (lambda p: p["telegram"]["reply_constraints"].update(text_only=True),
     "telegram.reply_constraints.text_only is not read"),
    (lambda p: p["admin_controls"].update(global_kill_switch=True),
     "admin_controls.global_kill_switch is not read"),
    (lambda p: p["callable_capabilities"]["telegram_reply"]["constraints"].append("no_files"),
     "constraint 'no_files', which the plugin does not enforce"),
    (lambda p: p["callable_capabilities"]["safe_search_lookup"].update(result_mode="snippets_only"),
     "callable_capabilities.safe_search_lookup.result_mode is not read"),
    (lambda p: p["callable_capabilities"]["voice_reply"].pop("skills"),
     "callable_capabilities.voice_reply.skills should list"),
    (lambda p: p["callable_capabilities"]["voice_reply"].update(enabled="yes"),
     "callable_capabilities.voice_reply.enabled should be true or false"),
    (lambda p: p.pop("callable_capabilities"), "callable_capabilities is missing"),
    (lambda p: p["internal_learning"]["durable_memory"].update(categories_allowed=["x"]),
     "internal_learning.durable_memory.categories_allowed is not read"),
    (lambda p: p.pop("internal_learning"),
     "remember is granted but internal_learning.durable_memory.categories_forbidden"),
    (lambda p: p["internal_learning"]["durable_memory"].update(categories_forbidden=[]),
     "remember is granted but internal_learning.durable_memory.categories_forbidden"),
])
def test_anything_the_code_does_not_act_on_is_reported(edit, expected):
    profile = shipped()
    edit(profile)
    assert any(expected in line for line in capabilities.problems(profile)), \
        capabilities.problems(profile)


def test_the_old_profile_is_refused():
    """The profile production ran with: it reads like a policy nobody enforced."""
    old = shipped()
    old["callable_capabilities"] = {
        "telegram_reply": {"enabled": True, "constraints": ["same_chat_only", "text_only"]},
    }
    old["internal_learning"] = {"learned_skills": {"classes_forbidden": ["code_execution"]}}
    found = capabilities.problems(old)
    assert any("telegram_reply.skills should list" in line for line in found)
    assert any("'text_only'" in line for line in found)
    assert any("internal_learning.learned_skills is not read" in line for line in found)


# The channel applies it.

def _fake_helper():
    helper = types.ModuleType("helper")
    helper.balance_parentheses = lambda s: '((send "hi") (shell "ls"))'
    helper.LLM_COMMANDS = {"send", "shell"}
    return helper


@pytest.fixture
def channel(monkeypatch):
    helper = _fake_helper()
    monkeypatch.setitem(sys.modules, "helper", helper)
    stopped = []
    monkeypatch.setattr(tm, "start_telegram", lambda token, chat_id: None)
    monkeypatch.setattr(tm, "stop_telegram", lambda: stopped.append(True))
    monkeypatch.setattr(tm.auth, "get_proxy_url", lambda: "")
    monkeypatch.setenv("TG_BOT_TOKEN", "placeholder")
    # load_config rewrites these on the shared channel; put them back after.
    for attr in ("granted_skills", "send_constraints", "profile_problems",
                 "reply_only_on_tag", "reply_on_reply", "dm_enabled",
                 "restrict_to_config_chat", "allow_group_bots", "allowed_chat_ids",
                 "allowed_chat_id", "admin_ids", "reply_constraints"):
        monkeypatch.setattr(tm._channel, attr, getattr(tm._channel, attr))
    return types.SimpleNamespace(helper=helper, stopped=stopped)


def test_starting_the_channel_installs_the_profiles_allowlist(channel, tmp_path):
    tm._channel.load_config(SHIPPED)
    tm.TelegramChannel().start()

    assert channel.stopped == []
    assert channel.helper.balance_parentheses("x").startswith('((send "hi") (Error SKILL_DISABLED_IN_TELEGRAM')


def test_a_profile_the_code_cannot_enforce_stops_the_bot(channel, tmp_path):
    profile = shipped()
    profile["callable_capabilities"]["telegram_reply"]["constraints"].append("text_only")
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile))
    tm._channel.load_config(str(path))

    with pytest.raises(ValueError, match="text_only"):
        tm.TelegramChannel().start()
    assert channel.stopped == [True]
    assert channel.helper.balance_parentheses("x") == '((send "hi") (shell "ls"))'


def test_a_missing_profile_stops_the_bot(channel, tmp_path):
    tm._channel.load_config(str(tmp_path / "absent.yaml"))

    with pytest.raises(ValueError, match="no profile at"):
        tm.TelegramChannel().start()
    assert channel.stopped == [True]


def test_loading_the_plugin_without_starting_the_channel_changes_nothing(channel, monkeypatch):
    """Another channel may be the active one; only Telegram's start installs it."""
    monkeypatch.setattr(tm.media_handler, "register_channel", lambda *a: None)
    monkeypatch.setattr(tm.channels, "registerCommChannel", lambda name, ch: None)

    tm.loadOmegaPlugin()

    assert channel.helper.balance_parentheses("x") == '((send "hi") (shell "ls"))'


def test_starting_the_channel_screens_remember(channel, monkeypatch):
    checked = []

    async def forbidden(text):
        checked.append(text)
        return "password" in text

    monkeypatch.setattr(tm.config_helper, "is_memory_forbidden", forbidden)
    channel.helper.balance_parentheses = lambda s: s
    tm._channel.load_config(SHIPPED)
    tm.TelegramChannel().start()

    kept = '((remember "Paul is in UTC+2"))'
    assert channel.helper.balance_parentheses(kept) == kept
    out = channel.helper.balance_parentheses('((remember "the admin password is hunter2"))')
    assert out.startswith("((Error MEMORY_CATEGORY_FORBIDDEN ")
    assert checked == ["Paul is in UTC+2", "the admin password is hunter2"]


def test_a_memory_check_that_fails_refuses_the_note(channel, monkeypatch):
    async def broken(text):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(tm.config_helper, "is_memory_forbidden", broken)
    assert tm._screen_memory("Paul is in UTC+2").startswith("(Error MEMORY_CATEGORY_FORBIDDEN ")


@pytest.mark.parametrize("skill", ["shell", "read-file", "write-file", "metta", "get-io-policy"])
def test_the_channel_reports_an_ungranted_skill_disabled(channel, skill):
    tm._channel.load_config(SHIPPED)
    assert tm.TelegramChannel().is_tool_disabled(skill) is True


def test_the_channel_still_allows_a_granted_skill(channel, monkeypatch):
    tm._channel.load_config(SHIPPED)
    monkeypatch.setattr(tm._channel, "search_disabled", False)
    assert tm.TelegramChannel().is_tool_disabled("send") is False
    assert tm.TelegramChannel().is_tool_disabled("websearch") is False
