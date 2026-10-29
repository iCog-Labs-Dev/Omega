"""A reply goes only to the chat being answered.

The model reads every chat's messages tagged with their ids and chooses the
target with a [chat_id] prefix. Nothing checked it, so one group's reply could
be posted into another group or an admin's DM.
"""
import pytest

import test_telegram  # noqa: F401  # stubs core's modules when run standalone
import telegram_channel as tm

CURRENT = -1001994655615
OTHER_ALLOWED = -1003951566228
NOT_ALLOWED = -4999149174


@pytest.fixture
def sent(monkeypatch):
    """Answer chat CURRENT; record what would be delivered instead of sending."""
    deliveries = []

    async def not_blocked(text):
        return False

    monkeypatch.setattr(tm, "is_category_blocked", not_blocked)
    monkeypatch.setattr(tm._channel, "chat_id", CURRENT)
    monkeypatch.setattr(tm._channel, "allowed_chat_ids", {str(CURRENT), str(OTHER_ALLOWED)})
    monkeypatch.setattr(tm._channel, "restrict_to_config_chat", True)
    monkeypatch.setattr(tm._channel, "admin_ids", [])
    monkeypatch.setattr(tm._channel, "send_constraints", frozenset({"same_chat_only"}))
    monkeypatch.setattr(
        tm._channel, "send_message",
        lambda text, chat_id=None, reply_to_id=None: deliveries.append((str(chat_id), reply_to_id, text)))
    return deliveries


@pytest.mark.parametrize("other", [OTHER_ALLOWED, NOT_ALLOWED])
def test_a_send_to_another_chat_is_refused(sent, other):
    result = tm.send_message(f"[{other}] [55] hello there")

    assert sent == []
    assert result.startswith("Error: Refused:")
    assert str(CURRENT) in result and str(other) in result


def test_a_send_to_the_current_chat_with_its_prefix_is_delivered(sent):
    assert tm.send_message(f"[{CURRENT}] [55] hello there") is None
    assert sent == [(str(CURRENT), 55, "hello there")]


def test_a_send_without_a_prefix_goes_to_the_current_chat(sent):
    assert tm.send_message("hello there") is None
    assert sent == [(str(CURRENT), None, "hello there")]


def test_the_model_sees_a_refused_send(sent):
    with pytest.raises(tm.SEND_REFUSED_ONLY_THE_CURRENT_CHAT_CAN_BE_ANSWERED,
                       match="cannot go to chat"):
        tm.TelegramChannel().send(f"[{OTHER_ALLOWED}] hello there")
    assert sent == []


def test_a_delivered_send_raises_nothing(sent):
    tm.TelegramChannel().send("hello there")
    assert sent == [(str(CURRENT), None, "hello there")]


def test_without_same_chat_only_another_allowed_chat_is_reachable(sent, monkeypatch):
    monkeypatch.setattr(tm._channel, "send_constraints", frozenset())
    assert tm.send_message(f"[{OTHER_ALLOWED}] hello there") is None
    assert sent == [(str(OTHER_ALLOWED), None, "hello there")]


def test_without_same_chat_only_a_chat_the_bot_does_not_serve_is_not(sent, monkeypatch):
    monkeypatch.setattr(tm._channel, "send_constraints", frozenset())
    with pytest.raises(tm.SEND_REFUSED_CHAT_NOT_ALLOWED, match="not one this bot serves"):
        tm.TelegramChannel().send(f"[{NOT_ALLOWED}] hello there")
    assert sent == []


def test_the_model_sees_an_ethics_refusal_too(sent, monkeypatch):
    async def blocked(text):
        return True

    monkeypatch.setattr(tm, "is_category_blocked", blocked)
    monkeypatch.setattr(tm, "alert_ethics_violation", lambda tool, text=None: None)

    with pytest.raises(tm.SEND_REFUSED_UNSAFE_CONTENT, match="Unsafe response content"):
        tm.TelegramChannel().send("something unsafe")
    assert sent == []
