import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vision as ov


class _FakeMessage:
    def __init__(self, content): self.content = content

class _FakeChoice:
    def __init__(self, content): self.message = _FakeMessage(content)

class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]

class _StatusError(Exception):
    """Stands in for an openai APIStatusError, which carries status_code."""
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code

class _FakeCompletions:
    def __init__(self, provider, calls, outcome):
        self._provider = provider
        self._calls = calls
        self._outcome = outcome
    def create(self, model, messages, **kwargs):
        self._calls.append({"provider": self._provider.name, "model": model,
                            "messages": messages, **kwargs})
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return _FakeResp(self._outcome)

class _FakeChat:
    def __init__(self, completions): self.completions = completions

class _FakeClient:
    def __init__(self, completions): self.chat = _FakeChat(completions)


PARTS = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]


def _clear_selection():
    for var in ("VISION_PROVIDER", "VISION_MODEL"):
        os.environ.pop(var, None)


def _stub_providers(outcomes):
    """Replace _make_client so each provider answers with outcomes[name]: a
    caption string, or an exception to raise. Returns the list of calls made
    and a function that restores the real client."""
    calls = []
    orig = ov._make_client

    def make(provider):
        outcome = outcomes[provider.name]
        if isinstance(outcome, RuntimeError) and str(outcome).startswith("not available"):
            raise outcome
        return _FakeClient(_FakeCompletions(provider, calls, outcome))

    ov._make_client = make
    return calls, lambda: setattr(ov, "_make_client", orig)


def test_vision_chat_builds_multimodal_request():
    _clear_selection()
    calls, restore = _stub_providers({"OpenRouter": "a red panda", "Anthropic": "unused"})
    try:
        out = ov.vision_chat(PARTS, "describe it")
        assert out == "a red panda", out
        content = calls[0]["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "describe it"}, content
        assert content[1] == PARTS[0], content
        # Anthropic's compat endpoint rejects a request without max_tokens.
        assert calls[0]["max_tokens"] == 1024, calls[0]
    finally:
        restore()


def test_defaults_to_openrouter_then_anthropic():
    _clear_selection()
    order = ov._provider_order()
    assert [p.name for p in order] == ["OpenRouter", "Anthropic"], order
    assert ov._model(order[0], is_first=True) == "anthropic/claude-haiku-4.5"
    assert ov._model(order[1], is_first=False) == "claude-haiku-4-5-20251001"


def test_openrouter_answer_means_anthropic_is_not_called():
    _clear_selection()
    calls, restore = _stub_providers({"OpenRouter": "a cat", "Anthropic": "unused"})
    try:
        assert ov.vision_chat(PARTS, "p") == "a cat"
        assert [c["provider"] for c in calls] == ["OpenRouter"], calls
    finally:
        restore()


def test_falls_back_to_anthropic_when_openrouter_refuses():
    # An OpenRouter account whose data policy excludes vision providers.
    _clear_selection()
    calls, restore = _stub_providers({
        "OpenRouter": _StatusError(404, "0 endpoints available matching your guardrail"),
        "Anthropic": "a dog",
    })
    try:
        assert ov.vision_chat(PARTS, "p") == "a dog"
        assert [c["provider"] for c in calls] == ["OpenRouter", "Anthropic"], calls
        assert calls[1]["model"] == "claude-haiku-4-5-20251001", calls[1]
    finally:
        restore()


def test_falls_back_when_the_first_answer_is_empty():
    _clear_selection()
    calls, restore = _stub_providers({"OpenRouter": "  ", "Anthropic": "a boat"})
    try:
        assert ov.vision_chat(PARTS, "p") == "a boat"
    finally:
        restore()


def test_falls_back_to_openrouter_when_anthropic_key_is_rejected():
    # QA's setup: Anthropic chosen first, its key rejected, OpenRouter working.
    _clear_selection()
    os.environ["VISION_PROVIDER"] = "Anthropic"
    calls, restore = _stub_providers({
        "Anthropic": _StatusError(401, "invalid x-api-key"),
        "OpenRouter": "a tree",
    })
    try:
        assert ov.vision_chat(PARTS, "p") == "a tree"
        assert [c["provider"] for c in calls] == ["Anthropic", "OpenRouter"], calls
    finally:
        restore()
        _clear_selection()


def test_every_provider_failing_names_each_one():
    _clear_selection()
    calls, restore = _stub_providers({
        "OpenRouter": _StatusError(404, "guardrail"),
        "Anthropic": _StatusError(401, "invalid x-api-key"),
    })
    try:
        try:
            ov.vision_chat(PARTS, "p")
        except RuntimeError as e:
            message = str(e)
        else:
            raise AssertionError("expected RuntimeError when every provider fails")
        assert "OpenRouter: HTTP 404: guardrail" in message, message
        assert "Anthropic: HTTP 401: invalid x-api-key" in message, message
    finally:
        restore()


def test_provider_and_model_are_selectable():
    _clear_selection()
    os.environ["VISION_PROVIDER"] = "Anthropic"
    try:
        order = ov._provider_order()
        assert [p.name for p in order] == ["Anthropic", "OpenRouter"], order
        os.environ["VISION_MODEL"] = "some/other-model"
        assert ov._model(order[0], is_first=True) == "some/other-model"
        # The override is for the chosen provider; a fallback keeps its own model.
        assert ov._model(order[1], is_first=False) == "anthropic/claude-haiku-4.5"
    finally:
        _clear_selection()


def test_unknown_provider_raises():
    _clear_selection()
    os.environ["VISION_PROVIDER"] = "Nope"
    try:
        raised = False
        try:
            ov._provider_order()
        except RuntimeError:
            raised = True
        assert raised, "expected RuntimeError for an unknown VISION_PROVIDER"
    finally:
        _clear_selection()


def test_missing_keys_are_reported_without_a_proxy():
    _clear_selection()
    saved = {k: os.environ.pop(k, None) for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GATEWAY_URL")}
    try:
        try:
            ov.vision_chat(PARTS, "p")
        except RuntimeError as e:
            message = str(e)
        else:
            raise AssertionError("expected RuntimeError when no key is set")
        assert "OpenRouter: not available (set OPENROUTER_API_KEY" in message, message
        assert "Anthropic: not available (set ANTHROPIC_API_KEY" in message, message
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_long_provider_errors_are_shortened():
    text = ov._describe_error(_StatusError(500, "x" * 1000))
    assert text.startswith("HTTP 500: xxx"), text
    assert len(text) < 400, len(text)
