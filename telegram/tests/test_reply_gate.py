"""Posts reach a chat only while someone there is waiting for an answer.

On staging the agent answered a message, then two turns later posted a
research status line ("#462 Data Pruning x RLCT L0 verified ...") into the
group. On singnet/Omega core, core itself posted "The agent didn't return an
answer: reasoning exceeded the token limit ..." on idle turns. Both are posts
nobody asked for.
"""
import pytest

import test_telegram  # noqa: F401  # stubs core's modules when run standalone
import core_notices
import media_handler
import telegram_channel as tm
from reply_gate import ReplyGate, WAITING_FOR_S

CHAT = -1001994655615
OTHER = -1003951566228

TOKEN_LIMIT = ("The agent didn't return an answer: reasoning exceeded the token limit "
               "for this response before it could produce one.\n\nIf you are the Omega "
               "administrator: raise 'maxOutputToken'.")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_startup_posts_pass_before_the_first_turn():
    # Core sends its version banner right after the channel starts.
    assert ReplyGate().check(CHAT) is None


def test_a_turn_with_no_message_may_not_post():
    gate = ReplyGate()
    gate.next_turn()
    refusal = gate.check(CHAT)
    assert refusal is not None and "waiting" in refusal.reason


def test_a_message_opens_the_chat_until_it_is_answered():
    gate = ReplyGate()
    gate.next_turn()
    gate.message_arrived(CHAT)
    assert gate.check(CHAT) is None
    gate.delivered(CHAT)
    # A long reply goes out in pieces within the same turn.
    assert gate.check(CHAT) is None
    gate.next_turn()
    assert gate.check(CHAT) is not None


def test_the_answer_may_come_after_a_lookup():
    gate = ReplyGate()
    gate.next_turn()
    gate.message_arrived(CHAT)
    gate.next_turn()  # the model queried memory first
    gate.next_turn()
    assert gate.check(CHAT) is None


def test_a_message_opens_only_its_own_chat():
    gate = ReplyGate()
    gate.next_turn()
    gate.message_arrived(OTHER)
    assert gate.check(CHAT) is not None
    assert gate.check(OTHER) is None


def test_an_unanswered_message_stops_counting_after_a_while():
    clock = Clock()
    gate = ReplyGate(clock=clock)
    gate.next_turn()
    gate.message_arrived(CHAT)
    clock.now += WAITING_FOR_S + 1
    assert gate.check(CHAT) is not None


def test_a_waiting_chat_hears_about_a_failure_once():
    gate = ReplyGate()
    gate.next_turn()
    assert gate.claim_notice(CHAT) is False
    gate.message_arrived(CHAT)
    assert gate.claim_notice(CHAT) is True
    assert gate.claim_notice(CHAT) is False


@pytest.fixture
def chat(monkeypatch):
    """Answer CHAT; record what would be delivered instead of sending, and
    let a test hand the agent a message the way a turn does."""
    deliveries = []

    async def not_blocked(text):
        return False

    monkeypatch.setattr(tm, "is_category_blocked", not_blocked)
    monkeypatch.setattr(tm._channel, "chat_id", CHAT)
    monkeypatch.setattr(tm._channel, "_start_typing", lambda chat_id: None)
    monkeypatch.setattr(tm._channel, "_is_allowed_chat", lambda chat_id: True)
    monkeypatch.setattr(tm._channel, "_message_queue", [])
    monkeypatch.setattr(
        tm._channel, "send_message",
        lambda text, chat_id=None, reply_to_id=None: deliveries.append(text))

    class Chat:
        sent = deliveries

        @staticmethod
        def idle_turn():
            assert tm._channel.get_last_message() == ""

        @staticmethod
        def message_turn(text="hi"):
            tm._channel._message_queue.append((CHAT, text, 7, None))
            assert tm._channel.get_last_message().endswith(text)

    return Chat


def test_research_after_a_reply_stays_out_of_the_chat(chat):
    chat.message_turn("what is RLCT?")
    tm.TelegramChannel().send("RLCT is a geometric invariant.")
    chat.idle_turn()
    chat.idle_turn()
    with pytest.raises(tm.SEND_REFUSED_NO_MESSAGE_TO_ANSWER, match="waiting"):
        tm.TelegramChannel().send("#462 Data Pruning x RLCT L0 verified (5788B on disk).")
    assert chat.sent == ["RLCT is a geometric invariant."]


def test_a_reply_in_parts_within_one_turn_is_delivered(chat):
    chat.message_turn()
    tm.TelegramChannel().send("first part")
    tm.TelegramChannel().send("second part")
    assert chat.sent == ["first part", "second part"]


def test_a_new_message_opens_the_chat_again(chat):
    chat.message_turn()
    tm.TelegramChannel().send("hello")
    chat.idle_turn()
    chat.message_turn("thanks")
    tm.TelegramChannel().send("you're welcome")
    assert chat.sent == ["hello", "you're welcome"]


@pytest.fixture
def core_with_notices(monkeypatch):
    """A core whose provider library has the token-limit and timeout notices;
    admin alerts are recorded instead of sent."""
    monkeypatch.setattr(core_notices, "_core_texts",
                        lambda: (TOKEN_LIMIT, "LLM request timed out at "))
    alerts = []
    monkeypatch.setattr(tm, "_alert_admins", lambda text, about: alerts.append(text))
    return alerts


def test_a_core_notice_on_an_idle_turn_goes_to_admins_only(chat, core_with_notices):
    chat.message_turn()
    tm.TelegramChannel().send("hello")
    chat.idle_turn()
    # Core's send writes line breaks as a literal backslash-n.
    tm.TelegramChannel().send(TOKEN_LIMIT.replace("\n", "\\n"))
    assert chat.sent == ["hello"]
    assert len(core_with_notices) == 1 and "maxOutputToken" in core_with_notices[0]


def test_a_waiting_user_hears_only_that_something_went_wrong(chat, core_with_notices):
    chat.message_turn("what can you do?")
    tm.TelegramChannel().send(TOKEN_LIMIT)
    tm.TelegramChannel().send(TOKEN_LIMIT)
    assert chat.sent == [tm._PUBLIC_NOTICE]
    # The real answer can still follow.
    tm.TelegramChannel().send("I can search the web and make PDFs.")
    assert chat.sent == [tm._PUBLIC_NOTICE, "I can search the web and make PDFs."]


def test_admins_hear_about_each_kind_once_in_a_while(chat, core_with_notices):
    chat.idle_turn()
    for _ in range(3):
        tm.TelegramChannel().send(TOKEN_LIMIT)
    tm.TelegramChannel().send("LLM request timed out at 10:01:02.003 (#1).")
    assert len(core_with_notices) == 2
    assert chat.sent == []


def test_a_core_without_notices_recognises_none(monkeypatch):
    monkeypatch.setattr(core_notices, "_core_texts", lambda: (None, None))
    assert core_notices.recognise(TOKEN_LIMIT) is None


@pytest.fixture
def media_channel(chat, monkeypatch):
    monkeypatch.setattr(media_handler, "_live_channel", tm._channel)
    monkeypatch.setattr(media_handler, "_image_generation_allowed", lambda: True)
    monkeypatch.setattr(media_handler, "_tts_allowed", lambda: True)
    monkeypatch.setattr(media_handler, "_pdf_generation_allowed", lambda: True)
    for paid in ("_generate_image_bytes", "prepare_speech", "_load_pdf_fonts"):
        monkeypatch.setattr(media_handler, paid,
                            lambda *a, **k: pytest.fail("work done for a post nobody asked for"))
    return chat


@pytest.mark.parametrize("call, code", [
    (lambda: media_handler.generate_and_send("a red panda reading"), "IMAGE_REFUSED_NO_MESSAGE_TO_ANSWER"),
    (lambda: media_handler.speak("good morning"), "VOICE_REFUSED_NO_MESSAGE_TO_ANSWER"),
    (lambda: media_handler.generate_and_send_pdf("Report"), "PDF_REFUSED_NO_MESSAGE_TO_ANSWER"),
])
def test_media_nobody_asked_for_is_not_made(media_channel, call, code):
    media_channel.idle_turn()
    assert call().startswith(code)
