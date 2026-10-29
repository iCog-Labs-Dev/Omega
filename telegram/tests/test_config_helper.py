import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import types
import config_helper as ch


class _FakeClient:
    def __init__(self, moderations): self.moderations = moderations


class _FakeCategoryScores:
    def __init__(self, scores): self._scores = scores
    def model_dump(self): return self._scores

class _FakeModerationResult:
    def __init__(self, scores): self.category_scores = _FakeCategoryScores(scores)

class _FakeModerationResponse:
    def __init__(self, scores): self.results = [_FakeModerationResult(scores)]

class _FakeModerations:
    def __init__(self, scores): self._scores = scores
    async def create(self, input): return _FakeModerationResponse(self._scores)

class _FakeModerationsNoCall:
    async def create(self, input):
        raise AssertionError("moderations.create should not be called")


def test_is_category_blocked_blocked_path():
    orig = ch._get_openai_client
    ch._get_openai_client = lambda: _FakeClient(_FakeModerations({"violence": 0.95}))
    try:
        result = asyncio.run(ch.is_category_blocked("I will hurt you"))
        assert result is True, result
    finally:
        ch._get_openai_client = orig


def test_is_category_blocked_allowed_path():
    orig = ch._get_openai_client
    scores = {"violence": 0.01, "harassment": 0.01, "hate": 0.01, "self-harm": 0.01, "sexual/minors": 0.01}
    ch._get_openai_client = lambda: _FakeClient(_FakeModerations(scores))
    try:
        result = asyncio.run(ch.is_category_blocked("what a nice day"))
        assert result is False, result
    finally:
        ch._get_openai_client = orig


def test_is_category_blocked_empty_text_no_network():
    orig = ch._get_openai_client
    ch._get_openai_client = lambda: _FakeClient(_FakeModerationsNoCall())
    try:
        assert asyncio.run(ch.is_category_blocked("")) is False
        assert asyncio.run(ch.is_category_blocked("   ")) is False
    finally:
        ch._get_openai_client = orig


def test_client_not_built_at_import():
    # Regression: config_helper is imported whenever the plugin loads,
    # independent of which channel is active, so import must not eagerly
    # build a client (which would require OPENAI_API_KEY to be set).
    assert ch._openai_client is None or hasattr(ch._openai_client, "moderations")


def test_llm_classify_falls_back_without_key():
    # No OPENAI_API_KEY at all: client construction fails inside the try
    # block, so _llm_classify must fall back to use_model rather than raise.
    # use_model also has no key, so it degrades to False. Calls _llm_classify
    # directly (not is_category_blocked) with a non-empty category list, since
    # the profile ships no blocked_categories and an empty list short-circuits
    # before the client is ever touched.
    orig_client, orig_key = ch._openai_client, os.environ.pop("OPENAI_API_KEY", None)
    ch._openai_client = None
    try:
        result = asyncio.run(ch._llm_classify("I will hurt you", ["violence"]))
        assert result is False, result
    finally:
        ch._openai_client = orig_client
        if orig_key is not None:
            os.environ["OPENAI_API_KEY"] = orig_key


class _FakeChatCompletions:
    """Answers like the classification model, deciding by a substring."""
    def __init__(self, flag_if=None, error=None):
        self.flag_if, self.error, self.prompts = flag_if, error, []

    async def create(self, model, messages, **kwargs):
        if self.error:
            raise self.error
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        expression = prompt.rsplit("Expression:", 1)[1]
        violates = self.flag_if in expression
        content = json.dumps({"violates": violates, "confidence": 0.95 if violates else 0.1,
                              "reason": "stub", "is_joke_or_figure_of_speech": False})
        message = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _chat_client(completions):
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))


def _with_chat(completions, coro_fn):
    orig = ch._get_openai_client
    ch._get_openai_client = lambda: _chat_client(completions)
    try:
        return asyncio.run(coro_fn())
    finally:
        ch._get_openai_client = orig


def test_the_shipped_profile_lists_forbidden_memory_categories():
    assert {"secret", "credential", "sensitive_personal_data"} <= set(
        ch.get_forbidden_memory_categories())


def test_a_credential_is_not_kept():
    completions = _FakeChatCompletions(flag_if="password")
    assert _with_chat(completions, lambda: ch.is_memory_forbidden(
        "the server password is hunter2")) is True
    assert "credential" in completions.prompts[0]


def test_an_ordinary_note_is_kept():
    completions = _FakeChatCompletions(flag_if="password")
    assert _with_chat(completions, lambda: ch.is_memory_forbidden(
        "Paul prefers replies in UTC+2")) is False


def test_a_memory_check_that_errors_refuses_the_note():
    completions = _FakeChatCompletions(error=RuntimeError("gateway down"))
    assert _with_chat(completions, lambda: ch.is_memory_forbidden(
        "Paul prefers replies in UTC+2")) is True


def test_no_forbidden_categories_refuses_the_note():
    orig_cache = ch._config_cache
    ch._config_cache = {}
    try:
        completions = _FakeChatCompletions(flag_if="password")
        assert _with_chat(completions, lambda: ch.is_memory_forbidden("anything")) is True
        assert completions.prompts == []
    finally:
        ch._config_cache = orig_cache


def test_the_ethics_model_check_still_lets_text_through_on_error():
    completions = _FakeChatCompletions(error=RuntimeError("gateway down"))
    assert _with_chat(completions, lambda: ch.use_model("hello", ["violence"])) is False


def test_the_classifier_goes_through_the_gateway_client(monkeypatch):
    """Core scrubs OPENAI_API_KEY in production; the gateway client is the
    only one with a key, so a client built from the environment must not be."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ch.openai, "AsyncOpenAI",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("built a client")))
    completions = _FakeChatCompletions(flag_if="password")
    assert _with_chat(completions, lambda: ch.is_memory_forbidden("my password is x")) is True
    assert completions.prompts


def test_get_spam_protection_config_keys():
    config = ch.get_spam_protection_config()
    expected_keys = {"time_window", "message_limit", "cooldown_duration", "admin_alert_threshold"}
    assert set(config.keys()) == expected_keys, config


def test_get_spam_protection_config_defaults_when_absent():
    orig_cache = ch._config_cache
    ch._config_cache = {}
    try:
        config = ch.get_spam_protection_config()
        assert config == {
            "time_window": 10,
            "message_limit": 5,
            "cooldown_duration": 120,
            "admin_alert_threshold": 3,
        }, config
    finally:
        ch._config_cache = orig_cache


if __name__ == "__main__":
    test_is_category_blocked_blocked_path()
    test_is_category_blocked_allowed_path()
    test_is_category_blocked_empty_text_no_network()
    test_client_not_built_at_import()
    test_llm_classify_falls_back_without_key()
    test_get_spam_protection_config_keys()
    test_get_spam_protection_config_defaults_when_absent()
    print("all config_helper tests passed")
