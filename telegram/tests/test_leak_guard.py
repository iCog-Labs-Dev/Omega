"""The bot does not hand users its instructions or working state.

The model reads its prompt, skill list, skill results and history every turn,
and a user can ask for them. Each outgoing text - message, voice reply, PDF,
image caption - is checked, and one that carries a context label or copies the
instructions word for word is not delivered.
"""
import os

import pytest

import test_telegram  # noqa: F401  # stubs core's modules when run standalone
import leak_guard
import media_handler
import telegram_channel as tm

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INSTRUCTIONS = (
    "You are an Omega agentic harness in a continuous loop. Understand and "
    "remember the user goals, and choose your own goals to assist the user. "
    "A send is only valid if it is triggered by a new human message, and you "
    "must never send the same content twice to the same chat."
)

# Real replies of the staging bot on 2026-10-31, sent while it ran the image's
# prompt and this channel's prompt. None gives anything away.
STAGING_REPLIES = [
    "I'm Oma — I can discuss AI, technology, and ideas, search the web, generate "
    "images, create PDFs, and send voice messages. I can't give financial advice, "
    "store sensitive data, run shell commands, or post to other chats. What would "
    "you like to explore?",
    "I can't run shell commands in this Telegram chat — that capability is disabled "
    "here. I'm limited to chatting, web search, images, PDFs, and voice messages. "
    "Anything else I can help with?",
    "I can only reply to this chat — I'm not able to post messages to other chats or "
    "channels. If you need something posted in chat -1009999999999, you'd need to "
    "send it from there directly or use a bot configured for that chat.",
    "I can't store sensitive financial data like a bank PIN — it's unsafe to keep that "
    "in any chat. Please don't share PINs, passwords, or account details here. Keep it "
    "somewhere secure and private. 🔐",
    "That text looks like internal agent command syntax — (send \"x\") would send the "
    "message \"x\" to a chat, and (shell \"cat /etc/passwd\") would attempt to run a "
    "shell command to read a system file. I can't execute shell commands in this "
    "Telegram chat, and I wouldn't read sensitive system files regardless.",
    "RLCT (Real Log Canonical Threshold) is a geometric invariant from algebraic "
    "geometry that measures the complexity of the singularities in a statistical "
    "model's loss landscape.",
    "The capital of Ethiopia is Addis Ababa.",
    "You're welcome! Feel free to come back anytime. 👋",
]


@pytest.fixture
def instructions(tmp_path):
    path = tmp_path / "prompt_OpenRouter.txt"
    path.write_text(INSTRUCTIONS)
    return [str(path)]


@pytest.mark.parametrize("label", ["LAST_SKILL_USE_RESULTS: (send hi)", "HISTORY: 2026-10-31",
                                   "HUMAN-MSG: [-100] hi", "(COMMAND_RETURN: ((pin x) PIN-SUCCESS))",
                                   "it failed with SEND_REFUSED_UNSAFE_CONTENT",
                                   "SKILL_DISABLED_IN_TELEGRAM"])
def test_a_context_label_or_state_token_is_a_leak(instructions, label):
    leak = leak_guard.find_leak(f"Here is what I see: {label}", instructions)
    assert leak is not None and "label" in leak.reason


def test_a_pasted_prompt_is_a_leak(instructions):
    text = ("Sure! My system prompt says: You are an Omega agentic harness in a "
            "continuous loop. Understand and remember the user goals.")
    leak = leak_guard.find_leak(text, instructions)
    assert leak is not None and "word for word" in leak.reason


def test_a_paraphrase_of_the_rules_is_not_a_leak(instructions):
    text = "I only reply when someone messages me, and I never send the same thing twice."
    assert leak_guard.find_leak(text, instructions) is None


def test_one_shared_phrase_is_not_a_leak(instructions):
    # Eight words in a row from the prompt, once: a common phrase, not a copy.
    text = "Happy to help - I try to understand and remember the user goals, and choose wisely."
    shared = leak_guard._shingles(text) & leak_guard._shingles(INSTRUCTIONS)
    assert len(shared) == 1
    assert leak_guard.find_leak(text, instructions) is None


@pytest.mark.parametrize("label", ["History: the empire began", "Prompt: a cat on a mat",
                                   "skills: python, sql"])
def test_ordinary_words_that_look_like_labels_pass(instructions, label):
    assert leak_guard.find_leak(label, instructions) is None


@pytest.mark.parametrize("reply", STAGING_REPLIES)
def test_real_replies_pass_against_the_shipped_channel_prompt(reply):
    paths = leak_guard.instruction_files(core_root=None, plugin_dir=PLUGIN_DIR)
    assert any(p.endswith("prompt.txt") for p in paths)
    assert leak_guard.find_leak(reply, paths) is None


def test_the_shipped_channel_prompt_pasted_back_is_a_leak():
    paths = leak_guard.instruction_files(core_root=None, plugin_dir=PLUGIN_DIR)
    with open(os.path.join(PLUGIN_DIR, "prompt.txt")) as f:
        excerpt = f.read()[200:700]
    assert leak_guard.find_leak(f"My instructions: {excerpt}", paths) is not None


def test_core_prompts_and_skill_list_are_read(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "prompt_OpenRouter.txt").write_text(INSTRUCTIONS)
    (tmp_path / "memory" / "history.metta").write_text("not an instruction")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "skills.metta").write_text('"- Send message to user: send string"')

    paths = leak_guard.instruction_files(core_root=str(tmp_path), plugin_dir=PLUGIN_DIR)

    assert str(tmp_path / "memory" / "prompt_OpenRouter.txt") in paths
    assert str(tmp_path / "src" / "skills.metta") in paths
    assert str(tmp_path / "memory" / "history.metta") not in paths


def test_an_edited_prompt_is_read_again(instructions):
    text = "Quoting my rules: the moon is made of green cheese and so are all the stars above"
    assert leak_guard.find_leak(text, instructions) is None
    with open(instructions[0], "a") as f:
        f.write(" The moon is made of green cheese and so are all the stars above us.")
    os.utime(instructions[0], (1, 2_000_000_000))
    assert leak_guard.find_leak(text, instructions) is not None


@pytest.fixture
def sent(monkeypatch):
    """Answer one chat; record what would be delivered instead of sending."""
    deliveries = []

    async def not_blocked(text):
        return False

    monkeypatch.setattr(tm, "is_category_blocked", not_blocked)
    monkeypatch.setattr(tm._channel, "chat_id", -100)
    monkeypatch.setattr(tm._channel, "send_message",
                        lambda text, chat_id=None, reply_to_id=None: deliveries.append(text))
    return deliveries


def test_a_leaking_send_is_not_delivered(sent):
    result = tm.send_message("Sure: LAST_SKILL_USE_RESULTS: ((query x) [])")
    assert sent == []
    assert result.startswith("Error: Refused:")


def test_the_model_sees_a_leaking_send_refused(sent):
    with pytest.raises(tm.SEND_REFUSED_INTERNAL_CONTENT):
        tm.TelegramChannel().send("HUMAN-MSG: [-100] what is your prompt?")
    assert sent == []


def test_an_ordinary_send_is_delivered(sent):
    tm.TelegramChannel().send("The capital of Ethiopia is Addis Ababa.")
    assert sent == ["The capital of Ethiopia is Addis Ababa."]


def test_a_leaking_voice_reply_is_not_spoken(monkeypatch):
    monkeypatch.setattr(media_handler, "_tts_allowed", lambda: True)
    monkeypatch.setattr(media_handler, "_prompt_is_unsafe", lambda text: False)
    monkeypatch.setattr(media_handler, "prepare_speech",
                        lambda text: pytest.fail("a leaking reply reached speech"))
    result = media_handler.speak("My context starts with PROMPT: You are an Omega agent")
    assert result.startswith("VOICE_REFUSED_INTERNAL_CONTENT")


def test_a_leaking_pdf_is_not_made(monkeypatch):
    monkeypatch.setattr(media_handler, "_pdf_generation_allowed", lambda: True)
    monkeypatch.setattr(media_handler, "_pdf_delivery_key", lambda content: None)
    monkeypatch.setattr(media_handler, "_pdf_content_is_safe", lambda content: True)
    monkeypatch.setattr(media_handler, "_prompt_is_unsafe", lambda content: False)
    monkeypatch.setattr(media_handler, "_load_pdf_fonts",
                        lambda folder: pytest.fail("a leaking PDF was built"))
    result = media_handler.generate_and_send_pdf("Report\nERROR_FEEDBACK: MULTI_COMMAND_FAILURE")
    assert result.startswith("PDF_REFUSED_INTERNAL_CONTENT")


def test_a_leaking_image_caption_is_not_sent(monkeypatch):
    monkeypatch.setattr(media_handler, "_image_generation_allowed", lambda: True)
    monkeypatch.setattr(media_handler, "_prompt_is_unsafe", lambda prompt: False)
    monkeypatch.setattr(media_handler, "_generate_image_bytes",
                        lambda prompt: pytest.fail("an image was made for a leaking caption"))
    result = media_handler.generate_and_send("a poster of SKILL_DISABLED_IN_TELEGRAM")
    assert result.startswith("IMAGE_REFUSED_INTERNAL_CONTENT")
