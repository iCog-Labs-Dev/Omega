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
    result = mh._generate_pdf_bytes("A short PDF report", mh._load_pdf_fonts(None))
    assert isinstance(result, bytes), result
    assert result.startswith(b"%PDF"), result[:16]

def test_generate_and_send_pdf_success():
    original = (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
                mh._generate_pdf_bytes, mh._live_send_document)
    sent = {}
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda content: False
    mh._generate_pdf_bytes = lambda content, fonts: b"%PDF-test"
    mh._live_send_document = lambda data, filename: sent.update(data=data, filename=filename)
    try:
        assert mh.generate_and_send_pdf("A concise report") == (
            "PDF_SENT: Omega_Document.pdf was delivered to the user. "
            "Do not call generate-pdf again for this request.")
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

    result = mh._generate_pdf_bytes(content, mh._load_pdf_fonts(None))

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
        assert result.startswith("PDF_SENT: "), result
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
    mh._generate_pdf_bytes = lambda content, fonts: rendered.append(content) or b"%PDF-test"
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
    extracted = _pdf_text(mh._generate_pdf_bytes(content, mh._load_pdf_fonts(None)))
    assert content in extracted, extracted


def test_generate_pdf_bytes_joins_arabic_letters():
    # Joined Arabic is drawn with contextual glyphs, which a PDF reader maps to
    # presentation forms; NFKC folds them back to the plain letters.
    import unicodedata
    content = "مرحبا بالعالم"
    extracted = unicodedata.normalize("NFKC", _pdf_text(mh._generate_pdf_bytes(content, mh._load_pdf_fonts(None))))
    assert content in extracted, extracted


def test_missing_characters_are_listed_once_in_order():
    missing = mh._load_pdf_fonts(None).missing("Hello 👋 世界 ✅ 世界 👋")
    assert missing == ["👋", "世", "界", "✅"], missing


def test_bundled_font_covers_supported_scripts_and_layout():
    content = "Tab\there\nПривет «мир» № 5 €\nשלום مرحبا Ελληνικά — “quotes” •"
    assert mh._load_pdf_fonts(None).missing(content) == []


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
    mh._generate_pdf_bytes = lambda content, fonts: rendered.append(content) or b"%PDF-test"
    mh._live_send_document = lambda *args, **kwargs: sent.append((args, kwargs))
    try:
        result = mh.generate_and_send_pdf("Hello 👋 世界 ✅")
        assert result == (
            "PDF_FAILED: the PDF fonts cannot draw 👋 (U+1F44B), 世 (U+4E16), "
            "界 (U+754C), ✅ (U+2705). Nothing was sent. Install a font that "
            "covers them with install-pdf-font and a Google Fonts family name, "
            "or remove them, then call generate-pdf again"), result
        assert rendered == []
        assert sent == []
    finally:
        (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
         mh._generate_pdf_bytes, mh._live_send_document) = original



def _box_glyph_ttf():
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0)); pen.lineTo((50, 700)); pen.lineTo((450, 700)); pen.lineTo((450, 0))
    pen.closePath()
    return pen.glyph()


def _write_test_font(path, chars, outlines="truetype", variable=False, colour=False):
    """Write a tiny font that draws a box for each of chars. outlines is
    "truetype" (glyf) or "cff", the kind fpdf2 embeds badly. variable adds a
    weight axis whose default is Thin, like Noto Sans SC. colour adds COLR and
    CPAL tables next to the outlines, like the Google Fonts Noto Color Emoji."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    names = [".notdef"] + [f"uni{ord(c):04X}" for c in chars]
    fb = FontBuilder(1000, isTTF=(outlines == "truetype"))
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c): f"uni{ord(c):04X}" for c in chars})
    if outlines == "truetype":
        fb.setupGlyf({name: _box_glyph_ttf() for name in names})
    else:
        def box():
            pen = T2CharStringPen(500, None)
            pen.moveTo((50, 0)); pen.lineTo((50, 700)); pen.lineTo((450, 700)); pen.lineTo((450, 0))
            pen.closePath()
            return pen.getCharString()
        fb.setupCFF("TestCFF", {"FullName": "TestCFF"}, {name: box() for name in names}, {})
    fb.setupHorizontalMetrics({name: (500, 50) for name in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    if colour:
        fb.setupCPAL([[(1.0, 0.0, 0.0, 1.0)]])
        fb.setupCOLR({name: [(name, 0)] for name in names[1:]})
    if variable:
        fb.setupFvar(axes=[("wght", 100, 100, 900, "Weight")], instances=[])
        fb.setupGvar({name: [] for name in names})
    fb.save(str(path))


def _test_font_bytes(chars, **options):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "font.ttf"
        _write_test_font(path, chars, **options)
        return path.read_bytes()


def test_pdf_fonts_without_a_folder_use_only_the_bundled_font():
    fonts = mh._load_pdf_fonts(None)
    assert fonts.main.path == mh.PDF_FONT_PATH
    assert fonts.fallbacks == ()


def test_pdf_fonts_read_font_files_from_the_folder_in_filename_order():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        _write_test_font(folder / "20-second.ttf", "म")
        _write_test_font(folder / "10-first.ttf", "न")
        _write_test_font(folder / ".hidden.ttf", "ि")
        (folder / "notes.txt").write_text("not a font")
        fonts = mh._load_pdf_fonts(folder)
        assert [font.path.name for font in fonts.fallbacks] == ["10-first.ttf", "20-second.ttf"]


def test_pdf_fonts_skip_files_that_would_not_draw():
    """A broken file or a font without TrueType outlines must not count as
    covering anything, or its characters would pass the check and vanish."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        _write_test_font(folder / "good.ttf", "न")
        _write_test_font(folder / "cff.otf", "म", outlines="cff")
        (folder / "broken.ttf").write_bytes(b"not really a font")
        fonts = mh._load_pdf_fonts(folder)
        assert [font.path.name for font in fonts.fallbacks] == ["good.ttf"]
        assert fonts.missing("नम") == ["म"]
        assert isinstance(mh._load_pdf_font(folder / "cff.otf"), mh.UnusablePdfFont)


def test_pdf_fonts_read_an_edited_font_again():
    import os
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "extra.ttf"
        _write_test_font(path, "न")
        assert mh._load_pdf_fonts(Path(folder)).missing("नम") == ["म"]
        _write_test_font(path, "नम")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        assert mh._load_pdf_fonts(Path(folder)).missing("नम") == []


def test_generate_pdf_bytes_draws_characters_from_a_fallback_font():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        _write_test_font(Path(folder) / "extra.ttf", "नम")
        fonts = mh._load_pdf_fonts(Path(folder))
        extracted = _pdf_text(mh._generate_pdf_bytes("Hello नम", fonts))
        assert "Hello" in extracted and "नम" in extracted, extracted


def test_generate_and_send_pdf_uses_the_configured_font_folder():
    import tempfile
    from pathlib import Path
    original = (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
                mh._live_send_document, mh._pdf_font_dir)
    sent = []
    mh._pdf_generation_allowed = lambda: True
    mh._prompt_is_unsafe = lambda content: False
    mh._live_send_document = lambda data, filename: sent.append(data)
    try:
        with tempfile.TemporaryDirectory() as folder:
            _write_test_font(Path(folder) / "extra.ttf", "नम")
            mh._pdf_font_dir = lambda: None
            assert mh.generate_and_send_pdf("Hello नम").startswith("PDF_FAILED: the PDF fonts cannot draw")
            assert sent == []
            mh._pdf_font_dir = lambda: Path(folder)
            assert mh.generate_and_send_pdf("Hello नम").startswith("PDF_SENT: ")
            assert "नम" in _pdf_text(sent[0])
    finally:
        (mh._pdf_generation_allowed, mh._prompt_is_unsafe,
         mh._live_send_document, mh._pdf_font_dir) = original


_METADATA = """
name: "Test Sans"
fonts {
  name: "Test Sans"
  style: "italic"
  weight: 400
  filename: "TestSans-Italic.ttf"
}
fonts {
  name: "Test Sans"
  style: "normal"
  weight: 700
  filename: "TestSans-Bold.ttf"
}
fonts {
  name: "Test Sans"
  style: "normal"
  weight: 400
  filename: "TestSans-Regular.ttf"
}
"""


def test_google_font_family_accepts_only_plain_names():
    family = mh.GoogleFontFamily.parse("  Noto  Sans Devanagari ")
    assert family == mh.GoogleFontFamily("Noto Sans Devanagari", "notosansdevanagari")
    assert family.file_url("METADATA.pb") == (
        "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/METADATA.pb")
    for text in ("", "   ", "../../etc", "https://evil.example/x.ttf", "Noto/Sans", "a" * 65, None):
        assert mh.GoogleFontFamily.parse(text) is None, text


def test_regular_font_filename_picks_the_upright_regular_file():
    assert mh._regular_font_filename(_METADATA) == "TestSans-Regular.ttf"
    variable = 'fonts {\n style: "normal"\n weight: 400\n filename: "TestSans[wdth,wght].ttf"\n}'
    assert mh._regular_font_filename(variable) == "TestSans[wdth,wght].ttf"


def test_regular_font_filename_refuses_paths_and_non_ttf_files():
    for filename in ("../../etc/passwd.ttf", "sub/dir.ttf", "TestSans-Regular.otf"):
        metadata = f'fonts {{\n style: "normal"\n weight: 400\n filename: "{filename}"\n}}'
        try:
            mh._regular_font_filename(metadata)
        except mh.FontInstallRefused:
            continue
        raise AssertionError(f"accepted {filename}")


class _FakeResponse:
    def __init__(self, status_code, chunks=()):
        self.status_code = status_code
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        return iter(self._chunks)


def _with_fake_requests(response, run):
    calls = []
    fake = types.ModuleType("requests")
    fake.get = lambda url, **kwargs: calls.append((url, kwargs)) or response
    saved = sys.modules.get("requests")
    sys.modules["requests"] = fake
    try:
        return run(), calls
    except mh.FontInstallRefused as refused:
        return refused, calls
    finally:
        if saved is not None:
            sys.modules["requests"] = saved
        else:
            del sys.modules["requests"]


def test_download_stays_on_google_fonts_and_refuses_redirects():
    url = mh.GOOGLE_FONTS_OFL_URL + "testsans/METADATA.pb"

    result, calls = _with_fake_requests(_FakeResponse(200, [b"ab", b"cd"]),
                                        lambda: mh._download_google_fonts_file(url, 10))
    assert result == b"abcd"
    assert calls[0][1]["allow_redirects"] is False, calls

    result, _ = _with_fake_requests(_FakeResponse(302),
                                    lambda: mh._download_google_fonts_file(url, 10))
    assert isinstance(result, mh.FontInstallRefused) and "302" in str(result), result

    result, calls = _with_fake_requests(_FakeResponse(200, [b"x"]),
                                        lambda: mh._download_google_fonts_file("https://evil.example/f.ttf", 10))
    assert isinstance(result, mh.FontInstallRefused), result
    assert calls == []


def test_download_stops_past_the_size_limit():
    url = mh.GOOGLE_FONTS_OFL_URL + "testsans/TestSans-Regular.ttf"
    result, _ = _with_fake_requests(_FakeResponse(200, [b"x" * 6, b"x" * 6]),
                                    lambda: mh._download_google_fonts_file(url, 10))
    assert isinstance(result, mh.FontInstallRefused), result


def test_static_pdf_font_bytes_pins_variable_fonts_and_refuses_unusable_ones():
    import io
    from fontTools.ttLib import TTFont

    pinned = TTFont(io.BytesIO(mh._static_pdf_font_bytes(_test_font_bytes("न", variable=True))))
    assert "fvar" not in pinned and "glyf" in pinned
    assert pinned["OS/2"].usWeightClass == 400

    for data in (_test_font_bytes("न", outlines="cff"), b"not a font"):
        try:
            mh._static_pdf_font_bytes(data)
        except mh.FontInstallRefused:
            continue
        raise AssertionError("accepted an unusable font")


def _install_with_fake_repository(folder, font_bytes, run, reported_size=None):
    """Run with install-pdf-font reading from a stand-in repository and
    writing to folder. reported_size is what a HEAD request says the font file
    weighs, by default its real size."""
    original = (mh._download_google_fonts_file, mh._google_fonts_file_size,
                mh._pdf_font_dir, mh._pdf_font_install_allowed)
    downloads = []

    def fake_download(url, max_bytes):
        downloads.append(url)
        if url.endswith("/METADATA.pb"):
            return _METADATA.encode()
        return font_bytes

    mh._download_google_fonts_file = fake_download
    mh._google_fonts_file_size = lambda url: reported_size if reported_size is not None else len(font_bytes)
    mh._pdf_font_dir = lambda: folder
    mh._pdf_font_install_allowed = lambda: True
    try:
        return run(), downloads
    finally:
        (mh._download_google_fonts_file, mh._google_fonts_file_size,
         mh._pdf_font_dir, mh._pdf_font_install_allowed) = original


def test_install_pdf_font_adds_a_font_that_generate_pdf_then_uses():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as root:
        folder = Path(root) / "fonts"
        result, downloads = _install_with_fake_repository(
            folder, _test_font_bytes("नम"), lambda: mh.install_pdf_font("Test Sans"))
        assert result == "FONT_INSTALLED: Test Sans, 2 more characters can now go in PDFs", result
        assert downloads == [mh.GOOGLE_FONTS_OFL_URL + "testsans/METADATA.pb",
                             mh.GOOGLE_FONTS_OFL_URL + "testsans/TestSans-Regular.ttf"]
        assert sorted(p.name for p in folder.iterdir()) == ["testsans.ttf"]
        assert mh._load_pdf_fonts(folder).missing("Hello नम") == []

        again, downloads = _install_with_fake_repository(
            folder, b"unused", lambda: mh.install_pdf_font("test sans"))
        assert again == "FONT_ALREADY_INSTALLED: test sans", again
        assert downloads == []


def test_install_pdf_font_refuses_without_writing_anything():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as root:
        folder = Path(root) / "fonts"

        result, _ = _install_with_fake_repository(
            folder, _test_font_bytes("न", outlines="cff"), lambda: mh.install_pdf_font("Test Sans"))
        assert result.startswith("FONT_INSTALL_FAILED: Test Sans: the font has no TrueType outlines"), result

        original_cap = mh.MAX_FONT_DIR_BYTES
        mh.MAX_FONT_DIR_BYTES = 100
        try:
            result, _ = _install_with_fake_repository(
                folder, _test_font_bytes("न"), lambda: mh.install_pdf_font("Test Sans"))
        finally:
            mh.MAX_FONT_DIR_BYTES = original_cap
        assert "the font folder would pass" in result, result

        assert mh.install_pdf_font("https://evil.example/x.ttf").startswith("FONT_INSTALL_FAILED: pass")
        assert not folder.exists() or list(folder.iterdir()) == []


def test_install_pdf_font_respects_the_profile_gate():
    original = mh._pdf_font_install_allowed
    mh._pdf_font_install_allowed = lambda: False
    try:
        assert mh.install_pdf_font("Noto Sans Thai") == (
            "FONT_INSTALL_DISABLED: installing PDF fonts is turned off")
    finally:
        mh._pdf_font_install_allowed = original


def test_pdf_font_install_gate_defaults_on_and_can_be_turned_off():
    original = mh._live_channel

    class FakeChannel:
        reply_constraints = {}

    mh._live_channel = FakeChannel()
    try:
        assert mh._pdf_font_install_allowed() is True
        FakeChannel.reply_constraints = {"allow_pdf_font_install": False}
        assert mh._pdf_font_install_allowed() is False
    finally:
        mh._live_channel = original


class _FakeChannel:
    """Stands in for the live Telegram channel: records what the user is
    told and what is queued back to the agent."""
    reply_constraints = {}

    def __init__(self):
        self.sent = []
        self.events = []

    def conversation(self):
        return -1001, 77

    def send_message(self, text, chat_id=None, reply_to_id=None):
        self.sent.append((chat_id, text))

    def queue_event(self, chat_id, reply_to_id, text):
        self.events.append((chat_id, reply_to_id, text))


def _with_fake_channel(run):
    """Run with a fake live channel and background work held until the
    test runs it, so the before and after of a download can be checked."""
    original = (mh._live_channel, mh._run_in_background)
    channel, held = _FakeChannel(), []
    mh._live_channel = channel
    mh._run_in_background = held.append
    try:
        return run(channel, held)
    finally:
        mh._live_channel, mh._run_in_background = original


def test_install_pdf_font_downloads_in_the_background_and_reports_back():
    import tempfile
    from pathlib import Path

    def scenario(channel, held):
        started = mh.install_pdf_font("Test Sans")
        assert started.startswith("FONT_INSTALL_STARTED: Test Sans is downloading"), started
        assert channel.sent == [(-1001, "Downloading the Test Sans font this PDF needs. "
                                        "I will send the PDF when it is ready.")]
        assert channel.events == [] and len(held) == 1

        again = mh.install_pdf_font("Test Sans")
        assert again.startswith("FONT_INSTALL_IN_PROGRESS: Test Sans"), again
        assert len(held) == 1 and len(channel.sent) == 1

        held[0]()
        assert channel.events == [(-1001, 77,
            "[install-pdf-font] FONT_INSTALLED: Test Sans, 2 more characters can now "
            "go in PDFs. Call generate-pdf again for the request that needed it.")]
        assert mh.install_pdf_font("Test Sans") == "FONT_ALREADY_INSTALLED: Test Sans"
        assert len(held) == 1

    with tempfile.TemporaryDirectory() as root:
        _install_with_fake_repository(
            Path(root) / "fonts", _test_font_bytes("नम"),
            lambda: _with_fake_channel(scenario))


def test_background_install_failure_still_reaches_the_agent():
    import tempfile
    from pathlib import Path

    def scenario(channel, held):
        assert mh.install_pdf_font("Test Sans").startswith("FONT_INSTALL_STARTED")
        held[0]()
        (_, _, note), = channel.events
        assert note.startswith("[install-pdf-font] FONT_INSTALL_FAILED: Test Sans: "
                               "the font has no TrueType outlines"), note
        assert note.endswith("Tell the user the PDF cannot include those characters.")
        assert "testsans" not in mh._fonts_installing

    with tempfile.TemporaryDirectory() as root:
        _install_with_fake_repository(
            Path(root) / "fonts", _test_font_bytes("न", outlines="cff"),
            lambda: _with_fake_channel(scenario))


def test_background_install_reports_back_even_when_the_worker_crashes():
    import tempfile
    from pathlib import Path
    original = mh._install_pdf_font_files

    def crash(family, filename):
        raise RuntimeError("disk on fire")

    def scenario(channel, held):
        assert mh.install_pdf_font("Test Sans").startswith("FONT_INSTALL_STARTED")
        held[0]()
        (_, _, note), = channel.events
        assert note.startswith("[install-pdf-font] FONT_INSTALL_FAILED: Test Sans"), note

    mh._install_pdf_font_files = crash
    try:
        with tempfile.TemporaryDirectory() as root:
            _install_with_fake_repository(
                Path(root) / "fonts", b"unused", lambda: _with_fake_channel(scenario))
    finally:
        mh._install_pdf_font_files = original


def test_colour_fonts_are_refused_at_install_and_skipped_at_load():
    """A colour emoji font has a glyf table and lists its emoji, but fpdf2
    draws nothing for them, so they would pass the check and vanish."""
    import tempfile
    from pathlib import Path

    colour = _test_font_bytes("न", colour=True)
    try:
        mh._static_pdf_font_bytes(colour)
    except mh.FontInstallRefused as refused:
        assert "colour font (COLR)" in str(refused), refused
    else:
        raise AssertionError("accepted a colour font")

    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "colour.ttf").write_bytes(colour)
        fonts = mh._load_pdf_fonts(Path(folder))
        assert fonts.fallbacks == ()
        assert fonts.missing("न") == ["न"]


def test_pdf_content_is_safe_refuses_cards_followed_or_preceded_by_numbers():
    for text in (
        "Test card 4242 4242 4242 4242 12 2027",
        "ref 1234567890123 4111 1111 1111 1111",
        "1234 5678 4111 1111 1111 1111",
        "4111-1111-1111-1111 exp 12/29",
        "card=4111111111111111",
    ):
        assert not mh._pdf_content_is_safe(text), text


def test_pdf_content_is_safe_allows_phone_numbers_isbns_and_event_times():
    for text in (
        "Event time 1728390000003 ms",
        "Call +49 30 123456 0006",
        "ISBN 978-1-4028-9462-6",
    ):
        assert mh._pdf_content_is_safe(text), text


def test_pdf_embeds_only_the_fallback_fonts_its_text_uses():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        _write_test_font(Path(folder) / "10-na.ttf", "न")
        _write_test_font(Path(folder) / "20-ma.ttf", "म")
        _write_test_font(Path(folder) / "30-na-again.ttf", "न")
        fonts = mh._load_pdf_fonts(Path(folder))
        assert len(fonts.fallbacks) == 3

        def used(text):
            return [f.path.name for f in fonts.needed_for(text).fallbacks]

        assert used("A plain Latin report") == []
        assert used("Hello न") == ["10-na.ttf"]
        assert used("नम") == ["10-na.ttf", "20-ma.ttf"]

        page = PdfReader(BytesIO(mh._generate_pdf_bytes("A plain Latin report", fonts))).pages[0]
        assert len(page["/Resources"]["/Font"]) == 1


def test_install_pdf_font_checks_the_family_before_telling_the_user():
    """A family that does not exist, or a file over the limit, is refused at
    once, without a "Downloading" message the user would then see fail."""
    import tempfile
    from pathlib import Path

    def missing_family(channel, held):
        original = mh._download_google_fonts_file

        def not_found(url, max_bytes):
            raise mh.FontInstallRefused("not found in the Google Fonts repository")

        mh._download_google_fonts_file = not_found
        try:
            result = mh.install_pdf_font("Not A Real Family")
        finally:
            mh._download_google_fonts_file = original
        assert result == ("FONT_INSTALL_FAILED: Not A Real Family: not found in "
                          "the Google Fonts repository"), result
        assert channel.sent == [] and held == []

    def too_big(channel, held):
        result = mh.install_pdf_font("Test Sans")
        assert result == ("FONT_INSTALL_FAILED: Test Sans: the font file is 51 MB, "
                          "over the 32 MB limit"), result
        assert channel.sent == [] and held == []
        assert "testsans" not in mh._fonts_installing

    with tempfile.TemporaryDirectory() as root:
        folder = Path(root) / "fonts"
        _install_with_fake_repository(folder, b"unused", lambda: _with_fake_channel(missing_family))
        _install_with_fake_repository(folder, b"unused", lambda: _with_fake_channel(too_big),
                                      reported_size=51 * 1024 * 1024)
        assert not folder.exists()


class _ConversationChannel:
    """A live channel answering the given user message in the given chat."""
    reply_constraints = {}

    def __init__(self, chat_id, message_id):
        self.chat_id, self.message_id = chat_id, message_id

    def conversation(self):
        return self.chat_id, self.message_id


def _with_pdf_channel(channel, send, run):
    original = (mh._prompt_is_unsafe, mh._live_send_document, mh._live_channel,
                dict(mh._delivered_pdfs))
    mh._prompt_is_unsafe = lambda content: False
    mh._live_send_document = send
    mh._live_channel = channel
    mh._delivered_pdfs.clear()
    try:
        run()
    finally:
        mh._prompt_is_unsafe, mh._live_send_document, mh._live_channel, delivered = original
        mh._delivered_pdfs.clear()
        mh._delivered_pdfs.update(delivered)


def test_generate_and_send_pdf_sends_each_document_once_per_user_message():
    """QA saw one request bring up to 16 copies of a PDF, each call after
    PDF_SENT and with no new message."""
    channel = _ConversationChannel(555, 10)
    sent = []

    def run():
        assert mh.generate_and_send_pdf("Quarterly report").startswith("PDF_SENT: ")
        assert mh.generate_and_send_pdf("Quarterly report") == (
            "PDF_ALREADY_SENT: the user already has this document for their "
            "current message, so it was not sent again. Do not call "
            "generate-pdf again for this request.")
        assert len(sent) == 1
        assert mh.generate_and_send_pdf("Annual report").startswith("PDF_SENT: ")
        assert len(sent) == 2
        channel.message_id = 11
        assert mh.generate_and_send_pdf("Quarterly report").startswith("PDF_SENT: ")
        assert len(sent) == 3
        channel.chat_id = 777
        assert mh.generate_and_send_pdf("Quarterly report").startswith("PDF_SENT: ")
        assert len(sent) == 4

    _with_pdf_channel(channel, lambda data, filename: sent.append(data), run)


def test_generate_and_send_pdf_sends_again_after_a_failed_delivery():
    attempts = []

    def flaky_send(data, filename):
        attempts.append(data)
        if len(attempts) == 1:
            raise RuntimeError("network down")

    def run():
        assert mh.generate_and_send_pdf("Status") == "PDF_FAILED: generated but could not send"
        assert mh.generate_and_send_pdf("Status").startswith("PDF_SENT: ")
        assert len(attempts) == 2

    _with_pdf_channel(_ConversationChannel(555, 20), flaky_send, run)


def test_generate_pdf_bytes_keeps_tabs_as_spaces():
    """fpdf2 draws nothing for a tab, so "Name\tScore" came out "NameScore"."""
    import re
    text = _pdf_text(mh._generate_pdf_bytes("Name\tScore\nAda\t91", mh._load_pdf_fonts(None)))
    # PDF readers differ on how many spaces they report, so only check that
    # the words are kept apart.
    assert re.search(r"Name\s+Score", text) and re.search(r"Ada\s+91", text), text

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
    test_missing_characters_are_listed_once_in_order()
    test_bundled_font_covers_supported_scripts_and_layout()
    test_describe_missing_characters_caps_the_list()
    test_generate_and_send_pdf_refuses_characters_the_font_cannot_draw()
    test_pdf_fonts_without_a_folder_use_only_the_bundled_font()
    test_pdf_fonts_read_font_files_from_the_folder_in_filename_order()
    test_pdf_fonts_skip_files_that_would_not_draw()
    test_pdf_fonts_read_an_edited_font_again()
    test_generate_pdf_bytes_draws_characters_from_a_fallback_font()
    test_generate_and_send_pdf_uses_the_configured_font_folder()
    test_google_font_family_accepts_only_plain_names()
    test_regular_font_filename_picks_the_upright_regular_file()
    test_regular_font_filename_refuses_paths_and_non_ttf_files()
    test_download_stays_on_google_fonts_and_refuses_redirects()
    test_download_stops_past_the_size_limit()
    test_static_pdf_font_bytes_pins_variable_fonts_and_refuses_unusable_ones()
    test_install_pdf_font_adds_a_font_that_generate_pdf_then_uses()
    test_install_pdf_font_refuses_without_writing_anything()
    test_install_pdf_font_respects_the_profile_gate()
    test_pdf_font_install_gate_defaults_on_and_can_be_turned_off()
    test_install_pdf_font_downloads_in_the_background_and_reports_back()
    test_background_install_failure_still_reaches_the_agent()
    test_background_install_reports_back_even_when_the_worker_crashes()
    test_colour_fonts_are_refused_at_install_and_skipped_at_load()
    test_pdf_content_is_safe_refuses_cards_followed_or_preceded_by_numbers()
    test_pdf_content_is_safe_allows_phone_numbers_isbns_and_event_times()
    test_pdf_embeds_only_the_fallback_fonts_its_text_uses()
    test_install_pdf_font_checks_the_family_before_telling_the_user()
    test_generate_and_send_pdf_sends_each_document_once_per_user_message()
    test_generate_and_send_pdf_sends_again_after_a_failed_delivery()
    test_generate_pdf_bytes_keeps_tabs_as_spaces()
    print("all media_handler tests passed")
