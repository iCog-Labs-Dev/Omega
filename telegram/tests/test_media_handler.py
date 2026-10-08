import io
import os, sys
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pypdf import PdfReader
from io import BytesIO
import media_handler as mh


def test_no_image_returns_marker():
    mh.clear_pending()
    mh.set_pending_media(None)
    assert mh.describe_image("anything") == "[NO_IMAGE: nothing is attached to describe]"


def test_describe_memoizes_per_turn():
    calls = {"n": 0}
    orig = mh._call_vision_model

    def fake(image_parts, prompt):
        calls["n"] += 1
        return "a red panda"

    mh._call_vision_model = fake
    try:
        mh.set_pending_media([{"type": "image_url",
                               "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
        r1 = mh.describe_image("")
        r2 = mh.describe_image("")
        assert r1 == r2 == "[IMAGE DESCRIPTION]\na red panda", r1
        assert calls["n"] == 1, calls["n"]

        # A reply's clear_pending() must NOT blind describe-image mid-turn.
        mh.clear_pending()
        assert mh.describe_image("") == "[IMAGE DESCRIPTION]\na red panda"
        # A new non-image message makes the prior image stale.
        mh.set_pending_media(None)
        assert mh.describe_image("") == "[NO_IMAGE: nothing is attached to describe]"
    finally:
        mh._call_vision_model = orig
        mh.clear_pending()
        mh.set_pending_media(None)


def test_describe_never_raises():
    def boom(image_parts, prompt):
        raise RuntimeError("network down")

    orig = mh._call_vision_model
    mh._call_vision_model = boom
    try:
        mh.set_pending_media([{"type": "image_url",
                               "image_url": {"url": "data:image/jpeg;base64,BBBB"}}])
        out = mh.describe_image("")
        assert out.startswith("[IMAGE_DESCRIPTION_FAILED:"), out
    finally:
        mh._call_vision_model = orig
        mh.clear_pending()
        mh.set_pending_media(None)


def test_describe_never_raises_on_malformed_media():
    # A producer could set a malformed value directly; describe_image must still
    # return a marker rather than propagate an exception.
    mh._pending_media = 12345          # not a list/None
    mh._describe_media = 12345
    try:
        out = mh.describe_image("")
        assert out.startswith("[IMAGE_DESCRIPTION_FAILED:"), out
    finally:
        mh.clear_pending()
        mh.set_pending_media(None)


def test_sanitize_image_roundtrips_to_jpeg():
    from PIL import Image

    src = io.BytesIO()
    Image.new("RGB", (1, 1)).save(src, format="PNG")
    out = mh.sanitize_image(src.getvalue())
    assert out and isinstance(out, bytes)
    reopened = Image.open(io.BytesIO(out))
    assert reopened.format == "JPEG"


def test_extract_pdf_text_success_with_stubbed_pypdf():
    class FakePage:
        def extract_text(self):
            return "known page text"

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"irrelevant bytes", "doc.pdf")
        assert "known page text" in out, out
        assert "[PDF: doc.pdf]" in out, out
    finally:
        del sys.modules["pypdf"]


def test_extract_pdf_text_failure_returns_marker_never_raises():
    # Deterministically exercise the failure path regardless of whether pypdf is
    # installed: stub PdfReader to raise. Must return a marker, never raise.
    class BoomReader:
        def __init__(self, buf):
            raise RuntimeError("corrupt pdf")

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = BoomReader
    saved = sys.modules.get("pypdf")
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"irrelevant bytes", "doc.pdf")
        assert out.startswith("[PDF: doc.pdf]"), out
        assert "Could not extract text" in out, out
    finally:
        if saved is not None:
            sys.modules["pypdf"] = saved
        else:
            del sys.modules["pypdf"]


def test_transcribe_audio_success_with_stubbed_openai_client():
    import openai

    class FakeTranscriptions:
        def create(self, **kwargs):
            assert kwargs["model"] == "openai/whisper-large-v3"
            return types.SimpleNamespace(text="hello world")

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    orig_openai_cls = openai.OpenAI
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    openai.OpenAI = lambda *a, **kw: FakeClient()
    try:
        out = mh.transcribe_audio(b"fake audio bytes", "voice.ogg")
        assert "hello world" in out, out
        assert "[AUDIO TRANSCRIPT: voice.ogg]" in out, out
    finally:
        openai.OpenAI = orig_openai_cls
        os.environ.pop("OPENROUTER_API_KEY", None)


def test_transcribe_audio_missing_key_returns_marker_never_raises():
    os.environ.pop("OPENROUTER_API_KEY", None)
    out = mh.transcribe_audio(b"fake audio bytes", "voice.ogg")
    assert out.startswith("[AUDIO TRANSCRIPT: voice.ogg]"), out


def test_generate_and_send_disabled():
    orig = mh._image_generation_allowed
    mh._image_generation_allowed = lambda: False
    try:
        out = mh.generate_and_send("a cat")
        assert out.startswith("IMAGE_DISABLED"), out
    finally:
        mh._image_generation_allowed = orig


def test_generate_and_send_unsafe():
    orig_allowed = mh._image_generation_allowed
    orig_unsafe = mh._prompt_is_unsafe
    mh._image_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda prompt: True
    try:
        out = mh.generate_and_send("a cat")
        assert out.startswith("Refused"), out
    finally:
        mh._image_generation_allowed = orig_allowed
        mh._prompt_is_unsafe = orig_unsafe


def test_generate_and_send_generation_failure():
    orig_allowed = mh._image_generation_allowed
    orig_unsafe = mh._prompt_is_unsafe
    orig_gen = mh._generate_image_bytes
    mh._image_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda prompt: False
    mh._generate_image_bytes = lambda prompt: None
    try:
        out = mh.generate_and_send("a cat")
        assert out.startswith("IMAGE_FAILED"), out
    finally:
        mh._image_generation_allowed = orig_allowed
        mh._prompt_is_unsafe = orig_unsafe
        mh._generate_image_bytes = orig_gen


def test_generate_and_send_success():
    orig_allowed = mh._image_generation_allowed
    orig_unsafe = mh._prompt_is_unsafe
    orig_gen = mh._generate_image_bytes
    orig_send, orig_chan = mh._live_send_photo, mh._live_channel

    sent = {}
    mh._image_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda prompt: False
    mh._generate_image_bytes = lambda prompt: b"img"
    mh.register_channel(lambda image_bytes, caption=None: sent.update(
        bytes=image_bytes, caption=caption), lambda *a, **k: None,
        lambda *a, **k: None, lambda *a, **k: None, object())
    try:
        out = mh.generate_and_send("a cat")
        assert out.startswith("IMAGE_SENT"), out
        assert sent["bytes"] == b"img", sent
    finally:
        mh._image_generation_allowed = orig_allowed
        mh._prompt_is_unsafe = orig_unsafe
        mh._generate_image_bytes = orig_gen
        mh._live_send_photo, mh._live_channel = orig_send, orig_chan


def test_generate_and_send_without_registered_channel():
    # Regression: the image generated but no channel was registered to send it.
    # Live runs hit this because the plugin loader execs plugin modules without
    # adding them to sys.modules, so looking the channel up by name found a
    # second, never-started copy.
    orig_allowed = mh._image_generation_allowed
    orig_unsafe = mh._prompt_is_unsafe
    orig_gen = mh._generate_image_bytes
    orig_send = mh._live_send_photo

    mh._image_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda prompt: False
    mh._generate_image_bytes = lambda prompt: b"img"
    mh._live_send_photo = None
    try:
        out = mh.generate_and_send("a cat")
        assert out == "IMAGE_FAILED: generated but no channel is registered to send it", out
    finally:
        mh._image_generation_allowed = orig_allowed
        mh._prompt_is_unsafe = orig_unsafe
        mh._generate_image_bytes = orig_gen
        mh._live_send_photo = orig_send


def test_register_channel_wires_gate_and_sender():
    orig_send, orig_chan = mh._live_send_photo, mh._live_channel
    try:
        class Chan:
            reply_constraints = {"allow_image_generation": True}
        mh.register_channel(lambda *a, **k: None, lambda *a, **k: None,
                            lambda *a, **k: None, lambda *a, **k: None, Chan())
        assert mh._image_generation_allowed() is True
        Chan.reply_constraints = {"allow_image_generation": False}
        assert mh._image_generation_allowed() is False
    finally:
        mh._live_send_photo, mh._live_channel = orig_send, orig_chan


def test_generate_and_send_empty_prompt():
    out = mh.generate_and_send("   ")
    assert out == "IMAGE_FAILED: empty prompt", out


# --- PDF generation skill tests -------------------------------------------

def test_generate_pdf_bytes_has_pdf_signature():
    result = mh._generate_pdf_bytes("A short PDF report")
    assert isinstance(result, bytes), result
    assert result.startswith(b"%PDF"), result[:16]

def test_generate_and_send_pdf_success():
    original = (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
                mh._generate_pdf_bytes, mh._live_send_document)
    sent = {}
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda content: False
    mh._generate_pdf_bytes = lambda content: b"%PDF-test"
    mh._live_send_document = lambda data, filename: sent.update(data=data, filename=filename)
    try:
        assert mh.generate_and_send_pdf("A concise report") == "PDF_SENT"
        assert sent == {"data": b"%PDF-test", "filename": "Omega_Document.pdf"}
    finally:
        (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
         mh._generate_pdf_bytes, mh._live_send_document) = original


def test_generate_and_send_pdf_checks_gate_and_content():
    original = mh._pdf_generation_allowed, mh._prompt_is_unsafe
    mh._pdf_generation_allowed = lambda: False
    mh._prompt_is_unsafe = lambda content: False
    try:
        assert mh.generate_and_send_pdf("report") == "PDF_DISABLED: PDF generation is turned off"
        assert mh.generate_and_send_pdf(" ") == "PDF_FAILED: empty content"
        mh._pdf_generation_allowed = lambda: True
        assert mh.generate_and_send_pdf("x" * (mh.MAX_PDF_CHARS + 1)) == (
            f"PDF_FAILED: content exceeds {mh.MAX_PDF_CHARS} characters")
    finally:
        mh._pdf_generation_allowed, mh._prompt_is_unsafe = original


def test_generate_and_send_pdf_refuses_unsafe_content():
    original = mh._pdf_generation_allowed, mh._prompt_is_unsafe
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda content: True
    try:
        assert mh.generate_and_send_pdf("unsafe report") == "Refused: unsafe PDF content"
    finally:
        mh._pdf_generation_allowed, mh._prompt_is_unsafe = original

def test_generate_pdf_bytes_preserves_supported_unicode_text():
    content = (
        'Curly quotes: “Hello”\n'
        'Em dash: —\n'
        'Bullet: •\n'
        'Cyrillic: Привет мир'
    )

    result = mh._generate_pdf_bytes(content)

    assert isinstance(result, bytes), result
    assert result.startswith(b"%PDF"), result[:16]

    reader = PdfReader(BytesIO(result))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "“Hello”" in extracted
    assert "—" in extracted
    assert "•" in extracted
    assert "Привет мир" in extracted

def test_pdf_generation_gate_defaults_and_overrides():
    orig_chan = mh._live_channel
    class FakeChannel:
        pass
    mh._live_channel = FakeChannel()
    try:
        # Missing key -> stays enabled for profiles created before this gate.
        FakeChannel.reply_constraints = {}
        assert mh._pdf_generation_allowed() is True

        # Explicit False -> False
        FakeChannel.reply_constraints = {"allow_pdf_generation": False}
        assert mh._pdf_generation_allowed() is False

        # Explicit True -> True
        FakeChannel.reply_constraints = {"allow_pdf_generation": True}
        assert mh._pdf_generation_allowed() is True
    finally:
        mh._live_channel = orig_chan



# --- speak skill tests -------------------------------------------------------

def test_speak_disabled():
    orig = mh._tts_allowed
    mh._tts_allowed = lambda: False
    try:
        out = mh.speak("hello")
        assert out.startswith("VOICE_DISABLED"), out
    finally:
        mh._tts_allowed = orig


def test_speak_synthesis_failure():
    orig_allowed = mh._tts_allowed
    orig_unsafe = mh._prompt_is_unsafe
    orig_synth = mh._synthesise_speech
    mh._tts_allowed = lambda: True
    mh._prompt_is_unsafe = lambda text: False
    mh._synthesise_speech = lambda text, voice: None
    try:
        out = mh.speak("hello")
        assert out.startswith("VOICE_FAILED"), out
    finally:
        mh._tts_allowed = orig_allowed
        mh._prompt_is_unsafe = orig_unsafe
        mh._synthesise_speech = orig_synth


def test_speak_success():
    orig_allowed = mh._tts_allowed
    orig_unsafe = mh._prompt_is_unsafe
    orig_synth = mh._synthesise_speech
    orig_send, orig_action, orig_chan = mh._live_send_voice, mh._live_send_chat_action, mh._live_channel

    actions = []
    sent = {}
    mh._tts_allowed = lambda: True
    mh._prompt_is_unsafe = lambda text: False
    mh._synthesise_speech = lambda text, voice: b"audio-bytes"
    mh._live_send_voice = lambda audio_bytes, caption=None: sent.update(bytes=audio_bytes)
    mh._live_send_chat_action = lambda action: actions.append(action)
    try:
        out = mh.speak("hello there")
        assert out == "VOICE_SENT", out
        assert sent["bytes"] == b"audio-bytes", sent
        assert "record_voice" in actions, actions
    finally:
        mh._tts_allowed = orig_allowed
        mh._prompt_is_unsafe = orig_unsafe
        mh._synthesise_speech = orig_synth
        mh._live_send_voice, mh._live_send_chat_action, mh._live_channel = orig_send, orig_action, orig_chan


def test_synthesise_speech_uses_edge_tts():
    calls = {}

    async def fake_stream():
        calls["called"] = True
        yield {"type": "audio", "data": b"mp3"}

    class FakeCommunicate:
        def __init__(self, text, voice):
            calls.update(text=text, voice=voice)
        def stream(self):
            return fake_stream()

    fake_edge_tts = types.ModuleType("edge_tts")
    fake_edge_tts.Communicate = FakeCommunicate
    original = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake_edge_tts
    try:
        result = mh._synthesise_speech("hello", "en-US-AriaNeural")
        assert result == b"mp3", result
        assert calls["text"] == "hello"
        assert calls["voice"] == "en-US-AriaNeural"
    finally:
        if original is None:
            del sys.modules["edge_tts"]
        else:
            sys.modules["edge_tts"] = original

def test_pdf_content_is_safe_blocks_credentials():
    # Real token formats must be refused
    assert not mh._pdf_content_is_safe("sk-or-v1-" + "a" * 64)
    assert not mh._pdf_content_is_safe("sk-ant-" + "a" * 93)
    assert not mh._pdf_content_is_safe("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert not mh._pdf_content_is_safe("sk-or-v1-" + "a" * 32 + "\u200b" + "a" * 32)


def test_pdf_content_is_safe_allows_normal_text():
    # Words like token/password in documentation must NOT be blocked
    assert mh._pdf_content_is_safe("Set the token field in your config.")
    assert mh._pdf_content_is_safe("If /etc/passwd contains root:x:0:0 ...")
    assert mh._pdf_content_is_safe("password must be at least 8 characters")
    assert mh._pdf_content_is_safe("The application reads /var/log/app.log.")


def test_pdf_content_is_safe_blocks_sensitive_file_references():
    assert not mh._pdf_content_is_safe("Read ~/.ssh/id_ed25519")
    assert not mh._pdf_content_is_safe("Configuration: /home/alice/.aws/credentials")
    assert not mh._pdf_content_is_safe("Environment: /proc/self/environ")
    assert not mh._pdf_content_is_safe("-----BEGIN PRIVATE KEY-----\nprivate material")

def test_pdf_content_is_safe_blocks_dotenv_and_shadow_and_windows_paths():
    assert not mh._pdf_content_is_safe("Config lives at ~/.env.production")
    assert not mh._pdf_content_is_safe("cat /etc/shadow")
    assert not mh._pdf_content_is_safe(r"C:\Users\alice\.aws\credentials")

def test_generate_and_send_pdf_success_with_real_bytes():
    # A real fpdf2-generated PDF must reach _live_send_document
    # (this catches the /OpenAction regression)
    original = (mh._pdf_generation_allowed, mh._prompt_is_unsafe, mh._live_send_document)
    sent = {}
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda c: False
    mh._live_send_document = lambda data, filename: sent.update(data=data, filename=filename)
    try:
        result = mh.generate_and_send_pdf("A normal report with token and /etc/passwd mentioned.")
        assert result == "PDF_SENT", result
        assert sent.get("data", b"").startswith(b"%PDF")
    finally:
        mh._pdf_generation_allowed, mh._prompt_is_unsafe, mh._live_send_document = original


def test_pdf_content_is_safe_blocks_credit_card_number():
    assert not mh._pdf_content_is_safe("Card: 4242 4242 4242 4242")


def test_pdf_content_is_safe_blocks_each_card_network():
    # Published test numbers, one per network the check knows.
    for number in (
        "4111111111111111",       # Visa
        "5555555555554444",       # Mastercard
        "2223003122003222",       # Mastercard 2-series
        "3782 822463 10005",      # Amex, grouped as printed
        "6011-1111-1111-1117",    # Discover, dashed
        "3530111333300000",       # JCB
        "30569309025904",         # Diners, 14 digits
        "6200000000000005",       # UnionPay
    ):
        assert not mh._pdf_content_is_safe(f"Pay with {number} today"), number


def test_pdf_content_is_safe_allows_ids_and_timestamps_that_pass_luhn():
    # Both pass Luhn and were refused as credit_card before the check looked
    # at how a number starts. QA hit them in ordinary bot output.
    for text in (
        "Group chat id: -1001234567896",
        "created_at_ms=1696680000001",
        "Order 1234567812345670 shipped",
    ):
        assert mh._pdf_content_is_safe(text), text


def test_generate_and_send_pdf_refuses_sensitive_content_without_sending():
    """Sensitive text must not be rendered or handed to Telegram."""
    original = (
        mh._pdf_generation_allowed,
        mh._prompt_is_unsafe,
        mh._generate_pdf_bytes,
        mh._live_send_document,
    )
    rendered = []
    sent = []
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda content: False
    mh._generate_pdf_bytes = lambda content: rendered.append(content) or b"%PDF-test"
    mh._live_send_document = lambda *args, **kwargs: sent.append((args, kwargs))
    try:
        for content in (
            "sk-or-v1-" + "a" * 64,
            "Card: 4242 4242 4242 4242",
            "Read ~/.ssh/id_ed25519",
        ):
            assert mh.generate_and_send_pdf(content) == "Refused: unsafe PDF content"
        assert rendered == []
        assert sent == []
    finally:
        (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
         mh._generate_pdf_bytes, mh._live_send_document) = original


def _pdf_text(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_generate_pdf_bytes_keeps_right_to_left_text_in_reading_order():
    # Without text shaping fpdf2 lays these out left to right, so a reader
    # sees the letters reversed: "שלום עולם" came out as "םלוע םולש".
    # Mixed Hebrew and English renders correctly too, but pypdf cannot read a
    # mixed-direction line back, so only a single-direction line is checked.
    content = "שלום עולם"
    extracted = _pdf_text(mh._generate_pdf_bytes(content))
    assert content in extracted, extracted


def test_generate_pdf_bytes_joins_arabic_letters():
    # Joined Arabic is drawn with contextual glyphs, which a PDF reader maps to
    # presentation forms; NFKC folds them back to the plain letters.
    import unicodedata
    content = "مرحبا بالعالم"
    extracted = unicodedata.normalize("NFKC", _pdf_text(mh._generate_pdf_bytes(content)))
    assert content in extracted, extracted


def test_characters_missing_from_pdf_font_lists_each_once_in_order():
    missing = mh._characters_missing_from_pdf_font("Hello 👋 世界 ✅ 世界 👋")
    assert missing == ["👋", "世", "界", "✅"], missing


def test_characters_missing_from_pdf_font_covers_supported_scripts_and_layout():
    content = "Tab\there\nПривет «мир» № 5 €\nשלום مرحبا Ελληνικά — “quotes” •"
    assert mh._characters_missing_from_pdf_font(content) == []


def test_describe_missing_characters_caps_the_list():
    missing = [chr(0x4E00 + i) for i in range(mh.MAX_LISTED_MISSING_CHARS + 3)]
    described = mh._describe_missing_characters(missing)
    assert described.startswith("一 (U+4E00), "), described
    assert described.endswith(" and 3 more"), described
    assert described.count("(U+") == mh.MAX_LISTED_MISSING_CHARS, described


def test_generate_and_send_pdf_refuses_characters_the_font_cannot_draw():
    """A PDF with characters silently left out must not go out as PDF_SENT."""
    original = (
        mh._pdf_generation_allowed,
        mh._prompt_is_unsafe,
        mh._generate_pdf_bytes,
        mh._live_send_document,
    )
    rendered = []
    sent = []
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda content: False
    mh._generate_pdf_bytes = lambda content: rendered.append(content) or b"%PDF-test"
    mh._live_send_document = lambda *args, **kwargs: sent.append((args, kwargs))
    try:
        result = mh.generate_and_send_pdf("Hello 👋 世界 ✅")
        assert result == (
            "PDF_FAILED: the PDF font cannot draw 👋 (U+1F44B), 世 (U+4E16), "
            "界 (U+754C), ✅ (U+2705). Nothing was sent; remove or replace these "
            "characters and call generate-pdf again"), result
        assert rendered == []
        assert sent == []
    finally:
        (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
         mh._generate_pdf_bytes, mh._live_send_document) = original


if __name__ == "__main__":
    test_no_image_returns_marker()
    test_describe_memoizes_per_turn()
    test_describe_never_raises()
    test_describe_never_raises_on_malformed_media()
    test_sanitize_image_roundtrips_to_jpeg()
    test_extract_pdf_text_success_with_stubbed_pypdf()
    test_extract_pdf_text_failure_returns_marker_never_raises()
    test_transcribe_audio_success_with_stubbed_openai_client()
    test_transcribe_audio_missing_key_returns_marker_never_raises()
    test_generate_and_send_disabled()
    test_generate_and_send_unsafe()
    test_generate_and_send_generation_failure()
    test_generate_and_send_success()
    test_generate_and_send_without_registered_channel()
    test_register_channel_wires_gate_and_sender()
    test_generate_and_send_empty_prompt()
    test_generate_pdf_bytes_has_pdf_signature()
    test_generate_and_send_pdf_success()
    test_generate_and_send_pdf_checks_gate_and_content()
    test_generate_and_send_pdf_refuses_unsafe_content()
    test_speak_disabled()
    test_speak_synthesis_failure()
    test_speak_success()
    test_synthesise_speech_uses_edge_tts()
    test_pdf_content_is_safe_blocks_credentials()
    test_pdf_content_is_safe_allows_normal_text()
    test_pdf_content_is_safe_blocks_sensitive_file_references()
    test_pdf_content_is_safe_blocks_dotenv_and_shadow_and_windows_paths()
    test_pdf_content_is_safe_blocks_credit_card_number()
    test_pdf_content_is_safe_blocks_each_card_network()
    test_pdf_content_is_safe_allows_ids_and_timestamps_that_pass_luhn()
    test_generate_and_send_pdf_success_with_real_bytes()
    test_generate_and_send_pdf_refuses_sensitive_content_without_sending()
    test_generate_pdf_bytes_preserves_supported_unicode_text()
    test_pdf_generation_gate_defaults_and_overrides()
    test_generate_pdf_bytes_keeps_right_to_left_text_in_reading_order()
    test_generate_pdf_bytes_joins_arabic_letters()
    test_characters_missing_from_pdf_font_lists_each_once_in_order()
    test_characters_missing_from_pdf_font_covers_supported_scripts_and_layout()
    test_describe_missing_characters_caps_the_list()
    test_generate_and_send_pdf_refuses_characters_the_font_cannot_draw()
    print("all media_handler tests passed")
