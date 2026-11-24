"""The release announcement, with core's config, channels and providers stubbed."""
import json
import sys
import types
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_notes as rn

TAG = rn.ReleaseTag("prod-2026-07-17")

PAYLOAD = {
    "tag_name": "prod-2026-07-17",
    "name": "Omega-prod-2026-07-17",
    "html_url": "https://github.com/iCog-Labs-Dev/Omega/releases/tag/prod-2026-07-17",
    "published_at": "2026-07-20T09:41:53Z",
    "body": "## [prod-2026-07-17]\n\n### Added\n- Telegram image support",
}


@pytest.fixture
def settings(monkeypatch):
    values = {}
    config = types.ModuleType("config")
    config.config_get_by_key = lambda key, default=None: values.get(key, default)
    monkeypatch.setitem(sys.modules, "config", config)
    return values


@pytest.fixture
def sent(monkeypatch):
    messages = []
    channels = types.ModuleType("channels")
    channels.commChannelSend = messages.append
    monkeypatch.setitem(sys.modules, "channels", channels)
    return messages


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / rn.MARKER_FILE
    monkeypatch.setattr(rn, "marker_path", lambda: path)
    return path


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(rn, "_tried", False)


@pytest.fixture
def released(settings, marker, sent, fresh):
    settings["releaseTag"] = TAG.name
    return settings


def test_the_tag_comes_from_the_release_tag_setting(settings):
    assert rn.ReleaseTag.configured() is None
    settings["releaseTag"] = "  prod-2026-07-17 \n"
    assert rn.ReleaseTag.configured() == TAG


def test_summary_request_carries_the_notes_and_the_link():
    request = rn.summary_request(TAG, PAYLOAD)
    assert rn.SUMMARY_INSTRUCTIONS in request
    assert f"Call {rn.TOOL_NAME} once" in request
    assert "VERSION: prod-2026-07-17" in request
    assert f"TITLE: {PAYLOAD['name']}" in request
    assert PAYLOAD["html_url"] in request
    assert request.endswith("- Telegram image support")


def test_nothing_to_summarize_without_notes():
    assert rn.summary_request(TAG, None) == ""
    assert rn.summary_request(TAG, {}) == ""
    assert rn.summary_request(TAG, dict(PAYLOAD, body="   ")) == ""


def test_the_published_notes_fit_whole():
    body = "x" * 6001
    assert rn.fit_notes(body) == body


def test_long_notes_are_trimmed_back_to_a_blank_line():
    paragraph = "x" * 100
    kept = rn.fit_notes("\n\n".join([paragraph] * 400))
    assert len(kept) <= rn.MAX_NOTES_CHARS
    assert kept.endswith(paragraph)
    assert "\n\n" in kept


def test_a_title_then_an_unbroken_bullet_run_keeps_the_bullets():
    body = "## [prod-2026-09-03]\n\n" + "\n".join(["- change " + "x" * 60] * 600)
    kept = rn.fit_notes(body)
    assert len(kept) >= rn.MAX_NOTES_CHARS * 4 // 5
    assert kept.count("- change ") > 200
    assert kept.endswith("x")


def test_notes_with_no_blank_line_fall_back_to_a_line_break():
    kept = rn.fit_notes("\n".join(["y" * 100] * 400))
    assert len(kept) <= rn.MAX_NOTES_CHARS
    assert kept.endswith("y")


def test_announcement_escapes_newlines_the_way_the_channel_expects():
    assert rn.announcement("Omega just updated\n- one thing\r\n- another") == \
        "Omega just updated\\n- one thing\\n- another"


def test_a_heading_after_bullets_keeps_its_gap():
    assert rn.announcement("- one\n- two\n\n**Fixes**\n\n- three") == \
        "- one\\n- two\\n​\\n\\n**Fixes**\\n\\n- three"


def test_a_heading_after_a_paragraph_is_left_alone():
    assert rn.announcement("An overview line.\n\n**Fixes**\n\n- one") == \
        "An overview line.\\n\\n**Fixes**\\n\\n- one"


def test_announcement_of_an_unusable_summary():
    assert rn.announcement("") == ""
    assert rn.announcement("   \n  ") == ""


def test_announce_sends_once_and_records_it(released, sent, marker, monkeypatch):
    monkeypatch.setattr(rn, "fetch_release", lambda tag: PAYLOAD)
    monkeypatch.setattr(rn, "_chat", lambda prompt: "Omega just updated\nsee the notes")

    assert rn.announce() == "Omega just updated\\nsee the notes"
    assert sent == ["Omega just updated\\nsee the notes"]
    assert marker.read_text().strip() == "prod-2026-07-17"

    # The heartbeat calls it again on every iteration; only the first works.
    assert rn.announce() == ""
    assert len(sent) == 1


def test_a_restart_does_not_announce_again(released, sent, marker, monkeypatch):
    marker.write_text("prod-2026-07-17\n")
    monkeypatch.setattr(rn, "fetch_release", lambda tag: pytest.fail("asked GitHub again"))

    assert rn.announce() == ""
    assert sent == []


def test_announce_stays_quiet_without_a_release_tag(settings, sent, marker, fresh, monkeypatch):
    monkeypatch.setattr(rn, "fetch_release",
                        lambda tag: pytest.fail("asked GitHub about a non-release"))

    assert rn.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_announce_stays_quiet_when_github_has_no_release(released, sent, marker, monkeypatch):
    monkeypatch.setattr(rn, "fetch_release", lambda tag: None)
    monkeypatch.setattr(rn, "_chat", lambda prompt: pytest.fail("asked the model"))

    assert rn.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_a_release_stays_unannounced_when_the_model_fails(released, sent, marker, monkeypatch):
    monkeypatch.setattr(rn, "fetch_release", lambda tag: PAYLOAD)

    def refuse(prompt):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(rn, "_chat", refuse)

    assert rn.announce() == ""
    assert sent == []
    assert not marker.exists()


def test_a_release_stays_unannounced_when_the_summary_is_empty(released, sent, marker, monkeypatch):
    monkeypatch.setattr(rn, "fetch_release", lambda tag: PAYLOAD)
    monkeypatch.setattr(rn, "_chat", lambda prompt: "   ")

    assert rn.announce() == ""
    assert sent == []
    assert not marker.exists()


class _Call:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _Response:
    def __init__(self, calls=(), error=None):
        self.calls, self.error = list(calls), error

    def is_error(self):
        return bool(self.error)


class _Builder:
    """Stands in for core's builder-style LLM classes."""

    def __init__(self):
        self.messages, self.tools, self.params = [], [], []

    def with_name(self, name):
        self.name = name
        return self

    def with_description(self, description):
        return self

    def add_parameter(self, parameter):
        self.params.append(parameter)
        return self

    def add_message(self, message):
        self.messages.append(message)
        return self

    def with_tools(self, tools):
        self.tools = tools
        return self


@pytest.fixture
def providers(monkeypatch):
    module = types.ModuleType("providers")
    module.LLMTool = module.LLMToolParameter = module.LLMRequest = _Builder
    module.llmRequestMessage = lambda role, content: (role, content)
    module.answer = _Response()
    module.requests = []

    def chat(request):
        module.requests.append(request)
        return module.answer

    module.llmProviderChat = chat
    monkeypatch.setitem(sys.modules, "providers", module)
    return module


def test_chat_offers_one_tool_and_reads_its_argument(providers):
    providers.answer = _Response([_Call(rn.TOOL_NAME, {rn.TOOL_ARGUMENT: "Updated!"})])

    assert rn._chat("the prompt") == "Updated!"
    request = providers.requests[0]
    assert request.messages == [("user", "the prompt")]
    assert [tool.name for tool in request.tools] == [rn.TOOL_NAME]
    assert [p.name for p in request.tools[0].params] == [rn.TOOL_ARGUMENT]


def test_chat_ignores_other_tools_and_raises_on_errors(providers):
    providers.answer = _Response([_Call("send", {"content": "hi"})])
    assert rn._chat("the prompt") == ""

    providers.answer = _Response(error="List of tool calls is empty")
    with pytest.raises(RuntimeError):
        rn._chat("the prompt")


def test_a_failed_fetch_leaves_nothing_to_announce(settings, monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(rn.urllib.request, "urlopen", refuse)
    assert rn.fetch_release(TAG) is None


class _Answer:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_fetch_reads_the_tag_endpoint(settings, monkeypatch):
    seen = {}

    def answer(request, timeout=None):
        seen["url"] = request.full_url
        return _Answer()

    monkeypatch.setattr(rn.urllib.request, "urlopen", answer)
    monkeypatch.setattr(rn.json, "load", lambda response: PAYLOAD)

    assert rn.fetch_release(TAG) == PAYLOAD
    assert seen["url"] == ("https://api.github.com/repos/iCog-Labs-Dev/Omega"
                           "/releases/tags/prod-2026-07-17")


def test_fetch_follows_the_configured_repository(settings, monkeypatch):
    seen = {}

    def answer(request, timeout=None):
        seen["url"] = request.full_url
        return _Answer()

    settings["releaseApiURL"] = "https://ghe.example.com/api/v3/"
    settings["releaseRepo"] = "/someone/elsewhere/"
    monkeypatch.setattr(rn.urllib.request, "urlopen", answer)
    monkeypatch.setattr(rn.json, "load", lambda response: PAYLOAD)

    rn.fetch_release(rn.ReleaseTag("v9.9.9"))
    assert seen["url"] == "https://ghe.example.com/api/v3/repos/someone/elsewhere/releases/tags/v9.9.9"


def test_the_plugin_announces_from_a_heartbeat():
    metta = (Path(rn.__file__).parent / "telegram.metta").read_text()
    assert "(add-heartbeat-listener telegram-release-notes" in metta
    assert "(py-call (release_notes.announce))" in metta
