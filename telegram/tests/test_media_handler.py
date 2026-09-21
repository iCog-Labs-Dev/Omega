import io
import os, sys
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
            return "known page text that has sufficient length to exceed the density threshold"

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"irrelevant bytes", "doc.pdf")
        assert "known page text that has sufficient length to exceed the density threshold" in out, out
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


def test_extract_pdf_text_uses_text_layer_without_vision_call():
    vision_called = []
    orig_vision = mh._call_vision_model
    mh._call_vision_model = lambda parts, prompt, **kw: vision_called.append((parts, prompt))

    class FakePage:
        def extract_text(self):
            return "direct text from pdf layer that is long enough to exceed the density threshold"

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"pdf bytes", "text_doc.pdf")
        assert "direct text from pdf layer that is long enough to exceed the density threshold" in out, out
        assert "[PDF: text_doc.pdf]" in out, out
        assert len(vision_called) == 0, "Vision model should not be called when text layer exists"
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision


def test_extract_pdf_text_scanned_pdf_renders_and_calls_vision():
    vision_calls = []
    orig_vision = mh._call_vision_model
    orig_render = mh._render_pdf_to_images

    def fake_vision(parts, prompt):
        vision_calls.append((parts, prompt))
        return "Transcribed OCR text from page 1"

    mh._call_vision_model = fake_vision
    mh._render_pdf_to_images = lambda b, max_p: (1, [b"fake-jpeg-bytes"])

    class FakePage:
        def extract_text(self):
            return ""  # Scanned PDF has no text layer

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"scanned bytes", "scanned.pdf")
        assert "[PDF: scanned.pdf]" in out, out
        assert "Transcribed OCR text from page 1" in out, out
        assert len(vision_calls) == 1
        assert "Transcribe all visible text" in vision_calls[0][1]
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision
        mh._render_pdf_to_images = orig_render


def test_extract_pdf_text_genuinely_blank_returns_blank_marker():
    orig_vision = mh._call_vision_model
    orig_render = mh._render_pdf_to_images

    mh._call_vision_model = lambda parts, prompt: "[BLANK]"
    mh._render_pdf_to_images = lambda b, max_p: (1, [b"fake-jpeg-bytes"])

    class FakePage:
        def extract_text(self):
            return ""

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"blank bytes", "blank.pdf")
        assert out == "[PDF: blank.pdf]\n[Document is blank]", out
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision
        mh._render_pdf_to_images = orig_render


def test_extract_pdf_text_zero_pages_returns_blank_marker():
    class EmptyPdfReader:
        def __init__(self, buf):
            self.pages = []

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = EmptyPdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"empty bytes", "empty.pdf")
        assert out == "[PDF: empty.pdf]\n[Document is blank]", out
    finally:
        del sys.modules["pypdf"]


def test_extract_pdf_text_page_cap_enforced_and_truncation_visible():
    vision_calls = []
    orig_vision = mh._call_vision_model
    orig_render = mh._render_pdf_to_images

    def fake_vision(parts, prompt):
        vision_calls.append(len(vision_calls) + 1)
        return f"Page {len(vision_calls)} content"

    mh._call_vision_model = fake_vision
    mh._render_pdf_to_images = lambda b, max_p: (5, [b"page1", b"page2"])

    class FakePage:
        def extract_text(self):
            return ""

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()] * 5

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"many pages", "large_scan.pdf", max_pages=2)
        assert len(vision_calls) == 2
        assert "Page 1 content" in out
        assert "Page 2 content" in out
        assert "[truncated: scanned pages limited to first 2 of 5 pages]" in out, out
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision
        mh._render_pdf_to_images = orig_render


def test_extract_pdf_text_with_real_pypdfium2_and_pypdf():
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    orig_vision = mh._call_vision_model
    try:
        # 1. Blank PDF returns blank marker
        mh._call_vision_model = lambda parts, prompt: "[BLANK]"
        out = mh.extract_pdf_text(pdf_bytes, "real_blank.pdf")
        assert out == "[PDF: real_blank.pdf]\n[Document is blank]", out

        # 2. Scanned page returns vision OCR text
        mh._call_vision_model = lambda parts, prompt: "Scanned invoice total: $150.00"
        out_ocr = mh.extract_pdf_text(pdf_bytes, "real_scan.pdf")
        assert "Scanned invoice total: $150.00" in out_ocr, out_ocr
        assert "[PDF: real_scan.pdf]" in out_ocr, out_ocr
    finally:
        mh._call_vision_model = orig_vision


def test_extract_pdf_text_thin_text_layer_triggers_ocr():
    vision_calls = []
    orig_vision = mh._call_vision_model
    orig_render = mh._render_pdf_to_images

    def fake_vision(parts, prompt, **kw):
        vision_calls.append((parts, prompt))
        return "Transcribed OCR text from page with watermark"

    mh._call_vision_model = fake_vision
    mh._render_pdf_to_images = lambda b, max_p: (1, [b"fake-jpeg-bytes"])

    class FakePage:
        def extract_text(self):
            return "CONFIDENTIAL"  # Thin text layer (< 50 chars/page) like watermark or stamp

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"scanned bytes", "watermarked_scan.pdf")
        assert "[PDF: watermarked_scan.pdf]" in out, out
        assert "Transcribed OCR text from page with watermark" in out, out
        assert len(vision_calls) == 1, "Vision model should be called when text layer is thin (< MIN_CHARS_PER_PAGE)"
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision
        mh._render_pdf_to_images = orig_render


def test_extract_pdf_text_page_failure_records_unreadable_and_continues():
    orig_vision = mh._call_vision_model
    orig_render = mh._render_pdf_to_images

    def fake_vision(parts, prompt, **kw):
        if getattr(fake_vision, "called", False):
            raise RuntimeError("429 rate limit or read timeout")
        fake_vision.called = True
        return "Page 1 transcribed content"

    mh._call_vision_model = fake_vision
    mh._render_pdf_to_images = lambda b, max_p: (2, [b"page1-bytes", b"page2-bytes"])

    class FakePage:
        def extract_text(self):
            return ""

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage(), FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"scanned bytes", "partial_fail.pdf")
        assert "[PDF: partial_fail.pdf]" in out, out
        assert "Page 1 transcribed content" in out, out
        assert "[page 2: unreadable]" in out, out
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision
        mh._render_pdf_to_images = orig_render


def test_extract_pdf_text_page_cap_preserved_when_max_chars_exceeded():
    orig_vision = mh._call_vision_model
    orig_render = mh._render_pdf_to_images

    mh._call_vision_model = lambda parts, prompt, **kw: "x" * 500
    mh._render_pdf_to_images = lambda b, max_p: (5, [b"page1", b"page2"])

    class FakePage:
        def extract_text(self):
            return ""

    class FakePdfReader:
        def __init__(self, buf):
            self.pages = [FakePage()] * 5

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakePdfReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = mh.extract_pdf_text(b"many pages", "large_scan.pdf", max_chars=100, max_pages=2)
        assert "[truncated: scanned pages limited to first 2 of 5 pages]" in out, out
        assert out.endswith("[truncated: scanned pages limited to first 2 of 5 pages]"), out
    finally:
        del sys.modules["pypdf"]
        mh._call_vision_model = orig_vision
        mh._render_pdf_to_images = orig_render


def test_call_vision_model_passes_max_tokens():
    import vision
    captured = {}
    orig = vision.vision_chat

    def fake_vision_chat(image_parts, prompt, max_tokens=1024):
        captured["max_tokens"] = max_tokens
        return "caption result"

    vision.vision_chat = fake_vision_chat
    try:
        res = mh._call_vision_model([], "prompt", max_tokens=4096)
        assert res == "caption result"
        assert captured["max_tokens"] == 4096
    finally:
        vision.vision_chat = orig


def test_render_pdf_to_images_sanitizes_dimensions():
    from pypdf import PdfWriter
    from PIL import Image
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=3000, height=3000)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    total_pages, rendered_pages = mh._render_pdf_to_images(pdf_bytes, max_pages=1)
    assert total_pages == 1
    assert len(rendered_pages) == 1
    img = Image.open(io.BytesIO(rendered_pages[0]))
    assert img.format == "JPEG"
    assert max(img.size) <= 2048, f"Rendered image size {img.size} exceeds 2048px limit"



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
        bytes=image_bytes, caption=caption), lambda *a, **k: None, lambda *a, **k: None, object())
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
        mh.register_channel(lambda *a, **k: None, lambda *a, **k: None, lambda *a, **k: None, Chan())
        assert mh._image_generation_allowed() is True
        Chan.reply_constraints = {"allow_image_generation": False}
        assert mh._image_generation_allowed() is False
    finally:
        mh._live_send_photo, mh._live_channel = orig_send, orig_chan


def test_generate_and_send_empty_prompt():
    out = mh.generate_and_send("   ")
    assert out == "IMAGE_FAILED: empty prompt", out


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


if __name__ == "__main__":
    test_no_image_returns_marker()
    test_describe_memoizes_per_turn()
    test_describe_never_raises()
    test_describe_never_raises_on_malformed_media()
    test_sanitize_image_roundtrips_to_jpeg()
    test_extract_pdf_text_success_with_stubbed_pypdf()
    test_extract_pdf_text_failure_returns_marker_never_raises()
    test_extract_pdf_text_uses_text_layer_without_vision_call()
    test_extract_pdf_text_scanned_pdf_renders_and_calls_vision()
    test_extract_pdf_text_genuinely_blank_returns_blank_marker()
    test_extract_pdf_text_zero_pages_returns_blank_marker()
    test_extract_pdf_text_page_cap_enforced_and_truncation_visible()
    test_extract_pdf_text_with_real_pypdfium2_and_pypdf()
    test_extract_pdf_text_thin_text_layer_triggers_ocr()
    test_extract_pdf_text_page_failure_records_unreadable_and_continues()
    test_extract_pdf_text_page_cap_preserved_when_max_chars_exceeded()
    test_call_vision_model_passes_max_tokens()
    test_render_pdf_to_images_sanitizes_dimensions()
    test_transcribe_audio_success_with_stubbed_openai_client()
    test_transcribe_audio_missing_key_returns_marker_never_raises()
    test_generate_and_send_disabled()
    test_generate_and_send_unsafe()
    test_generate_and_send_generation_failure()
    test_generate_and_send_success()
    test_generate_and_send_without_registered_channel()
    test_register_channel_wires_gate_and_sender()
    test_generate_and_send_empty_prompt()
    test_speak_disabled()
    test_speak_synthesis_failure()
    test_speak_success()
    test_synthesise_speech_uses_edge_tts()
    print("all media_handler tests passed")
