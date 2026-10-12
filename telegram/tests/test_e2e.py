"""End-to-end tests for the Telegram plugin against live services.

Every other test in this directory replaces the outside world with a FakeBot and
stubs. This file does the opposite: it talks to the real api.telegram.org, the
real vision, image, speech and transcription providers, and asserts on effects
that only a working integration can produce. It is the check that the plugin
still works as a plugin — loaded on its own, holding its own credentials, with
nothing of core mocked out.

The suite is opt-in. It costs money, posts to a real chat and needs network, so
it skips unless TG_E2E=1 and credentials are present. Credentials come from the
environment, or from a .env file: OMEGA_ENV_FILE if set, otherwise the .env
beside the repository root above this plugin. Values already in the environment
win, so CI can inject secrets without a file.

    pip install -r plugins/telegram/requirements.txt edge-tts
    TG_E2E=1 pytest plugins/telegram/tests/test_e2e.py -v -s

edge-tts is installed separately because the plugin imports it but declares it
only in core's root requirements; speak fails without it.

Run this against an idle bot token. Starting the channel begins polling
getUpdates, and Telegram gives an update to one poller only, so a deployed bot
sharing the token will lose messages for as long as the suite runs.

What each feature is checked by:

    plain reply delivery ............ test_plain_text_reply_is_delivered
    4096-char reply splitting ...... test_long_reply_is_split_and_delivered
    describe-image (vision) ........ test_describe_image_reads_a_real_image
    generate-image ................. test_generate_image_sends_a_photo
    speak (edge-tts + sendVoice) ... test_speak_sends_a_voice_message
    voice transcription ............ test_voice_is_synthesised_then_transcribed
    PDF text extraction ............ test_pdf_text_is_extracted
    Telegram file download ......... test_uploaded_file_can_be_downloaded_back
    websearch admin gate ........... test_search_toggle_gates_websearch
    proxy/gateway routing .......... test_gateway_routing_is_used_behind_a_proxy

What this shape cannot reach, and why: a bot cannot read its own outgoing
messages and Telegram has no API to read a chat as a third party, so nothing
here observes a reply the way a human user would. Inbound delivery and the
agent's own reasoning loop are therefore out of scope; the unit suite covers the
inbound handlers with synthetic messages, and a full round trip needs a second
"driver" bot (see Autotests/mock_telegram/) or a user-API client.
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_PLUGIN_DIR))

# Same path setup as the unit suite, minus its standalone stubs: these tests
# need the real channels/config/auth modules, not stand-ins.
for _path in (_REPO_ROOT,
              os.path.join(_REPO_ROOT, "channels"),
              os.path.join(_REPO_ROOT, "src"),
              _PLUGIN_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

TELEGRAM_API = "https://api.telegram.org"

# A word the vision model has to read back out of a generated image. Kept short,
# upper-case and unambiguous so the assertion does not depend on model prose.
IMAGE_MARKER = "OMEGA"
# Spoken and transcribed. Two common words; whisper returns them reliably even
# with punctuation and casing of its own choosing.
SPEECH_MARKER = "orange elephant"
PDF_MARKER = "OmegaPluginPdfMarker"


def _load_env_file():
    """Read KEY=VALUE lines from the .env file into the environment.

    Values already set in the environment are left alone, so an injected secret
    always beats a file on disk. Missing file is not an error - the credentials
    may come entirely from the environment.
    """
    path = os.environ.get("OMEGA_ENV_FILE") or os.path.join(_REPO_ROOT, ".env")
    if not os.path.isfile(path):
        return path, False
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value and key not in os.environ:
                os.environ[key] = value
    return path, True


_ENV_PATH, _ENV_FOUND = _load_env_file()


def _require(name):
    value = (os.environ.get(name) or "").strip()
    if not value:
        pytest.skip(f"{name} is not set (looked in the environment and {_ENV_PATH})")
    return value


pytestmark = pytest.mark.skipif(
    os.environ.get("TG_E2E") != "1",
    reason="live end-to-end suite: set TG_E2E=1 to run (real API calls, real chat, costs money)",
)


def _api(token, method, params=None, files=None, timeout=30):
    """Call one Bot API method and return its `result`.

    Used for the setup steps a test needs to perform as the bot itself - uploading
    a file to get a file_id, or probing getMe - rather than through the plugin.
    """
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    if files:
        boundary = "----omega-e2e-boundary"
        body = io.BytesIO()

        def _write(text):
            body.write(text.encode("utf-8"))

        for key, value in (params or {}).items():
            _write(f"--{boundary}\r\n")
            _write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n')
        for key, (filename, payload, content_type) in files.items():
            _write(f"--{boundary}\r\n")
            _write(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n')
            _write(f"Content-Type: {content_type}\r\n\r\n")
            body.write(payload)
            _write("\r\n")
        _write(f"--{boundary}--\r\n")
        request = urllib.request.Request(
            url, data=body.getvalue(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    else:
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not payload.get("ok"):
        raise AssertionError(f"{method} failed: {payload.get('description')}")
    return payload.get("result")


def _png_with_text(word):
    """Render `word` in black on white, large enough for a vision model to read."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (640, 240), "white")
    draw = ImageDraw.Draw(image)
    # The default bitmap font is tiny, so scale a small render up rather than
    # depending on a TrueType file being installed in the image.
    patch = Image.new("RGB", (160, 60), "white")
    ImageDraw.Draw(patch).text((6, 20), word, fill="black")
    image.paste(patch.resize((640, 240), Image.LANCZOS), (0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _tiny_pdf(marker):
    """Build a one-page PDF whose only text is `marker`.

    Written by hand with computed xref offsets so the suite needs no PDF writer
    beyond the pypdf the plugin already depends on for reading.
    """
    content = f"BT /F1 24 Tf 40 120 Td ({marker}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def _looks_like_image(blob):
    """True when the bytes start with a JPEG or PNG signature."""
    return blob.startswith(b"\xff\xd8\xff") or blob.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.fixture(scope="session")
def credentials():
    return {"token": _require("TG_BOT_TOKEN"), "chat_id": _require("TG_CHAT_ID")}


@pytest.fixture(scope="session")
def live(credentials):
    """A started channel, connected to real Telegram, registered with media_handler.

    Session-scoped: starting the channel spins up an aiogram polling loop in its
    own thread, and one is enough for the whole run.
    """
    import media_handler
    import telegram_channel as tc

    tc.start_telegram(credentials["token"], credentials["chat_id"])
    deadline = time.time() + 30
    while time.time() < deadline and not tc._channel.connected:
        time.sleep(0.5)
    if not tc._channel.connected:
        tc.stop_telegram()
        pytest.fail("channel did not connect to Telegram within 30s")
    media_handler.register_channel(
        tc.send_photo, tc.send_voice, tc.send_document, tc.send_chat_action,
        tc._channel)
    try:
        yield tc
    finally:
        tc.stop_telegram()


def test_bot_token_reaches_telegram(credentials):
    me = _api(credentials["token"], "getMe")
    assert me.get("is_bot") is True, me
    assert me.get("username"), me
    print(f"\n[e2e] agent bot is @{me['username']}", flush=True)


def test_plain_text_reply_is_delivered(live):
    """A reply that leaves the outbox has been accepted by Telegram.

    The channel queues on failure and drains on success, so an empty outbox
    afterwards is the delivery signal - there is no message id to inspect,
    because send_message deliberately returns nothing.
    """
    live.send_message(f"[e2e] plain reply {int(time.time())}")
    assert len(live._channel._outbox) == 0, "message stayed queued, so delivery failed"


def test_long_reply_is_split_and_delivered(live):
    """A reply past Telegram's 4096-char limit is split, and every part lands."""
    paragraph = ("Omega end-to-end splitting check. " * 40).strip()
    long_text = "\n\n".join(paragraph for _ in range(6))
    assert len(long_text) > live.TELEGRAM_TEXT_LIMIT, "test text is not long enough to split"

    parts = live.split_for_telegram(long_text)
    assert len(parts) > 1, f"expected a split, got {len(parts)} part(s)"
    for part in parts:
        assert len(part) <= live.TELEGRAM_TEXT_LIMIT, f"part is {len(part)} chars"

    live.send_message(long_text)
    assert len(live._channel._outbox) == 0, "a split part stayed queued"


@pytest.mark.parametrize("provider", ["Anthropic", "OpenRouter"])
def test_describe_image_reads_a_real_image(provider, monkeypatch):
    """describe-image captions a real image through a real vision provider.

    Parametrized over every provider in the table so a broken key or a retired
    model shows up as a named failure rather than a silent fallback.
    """
    import media_handler
    import vision

    key_env = vision.VISION_PROVIDERS[provider]["key_env"]
    if not (os.environ.get(key_env) or "").strip():
        pytest.skip(f"{key_env} is not set, so {provider} vision cannot be reached")
    monkeypatch.setenv("VISION_PROVIDER", provider)

    data_uri = media_handler.image_to_data_uri(_png_with_text(IMAGE_MARKER), "image/png")
    media_handler.set_pending_media(
        [{"type": "image_url", "image_url": {"url": data_uri}}])
    try:
        description = media_handler.describe_image("what word is written in the image?")
    finally:
        media_handler.set_pending_media(None)

    assert not description.startswith("[IMAGE_DESCRIPTION_FAILED"), description
    assert not description.startswith("[NO_IMAGE"), description
    assert IMAGE_MARKER.lower() in description.lower(), (
        f"{provider} did not read {IMAGE_MARKER!r} back: {description!r}")


def test_generate_image_sends_a_photo(live):
    """generate-image produces a real image and delivers it to the chat."""
    import media_handler
    assert media_handler._image_generation_allowed(), (
        "allow_image_generation is off in the loaded profile, so nothing would be generated")
    result = media_handler.generate_and_send(
        "a single solid red circle centred on a plain white background")
    assert isinstance(result, str), result
    lowered = result.lower()
    assert "fail" not in lowered and "refus" not in lowered and "error" not in lowered, result
    print(f"\n[e2e] generate-image returned {result!r}", flush=True)


def test_speak_sends_a_voice_message(live):
    """speak synthesises audio and delivers it as a Telegram voice message.

    edge-tts is imported by media_handler but declared only in core's root
    requirements, so on a base image without it this fails with VOICE_FAILED -
    which is the packaging gap, not a plugin bug.
    """
    import media_handler
    assert media_handler._tts_allowed(), (
        "allow_voice_reply is off in the loaded profile, so nothing would be spoken")
    result = media_handler.speak("Omega end to end test, voice reply working.")
    assert result == "VOICE_SENT", result


def test_voice_is_synthesised_then_transcribed():
    """A real voice clip round-trips through synthesis and transcription."""
    import media_handler
    audio = media_handler._synthesise_speech(
        f"The password is {SPEECH_MARKER}.", media_handler.DEFAULT_TTS_VOICE)
    if not audio:
        pytest.skip("edge-tts produced no audio (module missing or service unreachable)")
    transcript = media_handler.transcribe_audio(audio, "e2e-voice.mp3")
    assert "too large" not in transcript.lower(), transcript
    for word in SPEECH_MARKER.split():
        assert word in transcript.lower(), f"{word!r} missing from transcript: {transcript!r}"


def test_pdf_text_is_extracted():
    """PDF text extraction runs locally and finds the document's only string."""
    import media_handler
    extracted = media_handler.extract_pdf_text(_tiny_pdf(PDF_MARKER), "e2e.pdf")
    assert PDF_MARKER in extracted, extracted


def test_uploaded_file_can_be_downloaded_back(live, credentials):
    """The plugin's live bot can fetch a Telegram-hosted file by file_id.

    This is the leg the unit suite fakes: getFile plus the file download that
    every inbound photo, document and voice note depends on.
    """
    import asyncio
    png = _png_with_text(IMAGE_MARKER)
    sent = _api(credentials["token"], "sendPhoto",
                params={"chat_id": credentials["chat_id"],
                        "caption": "[e2e] download round-trip"},
                files={"photo": ("e2e.png", png, "image/png")})
    file_id = sorted(sent["photo"], key=lambda p: p.get("file_size", 0))[-1]["file_id"]

    bot = live._channel.bot
    loop = live._channel.loop

    async def _fetch():
        info = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download(info, destination=buffer)
        return buffer.getvalue()

    downloaded = asyncio.run_coroutine_threadsafe(_fetch(), loop).result(timeout=60)
    assert len(downloaded) > 0, "downloaded an empty file"
    assert _looks_like_image(downloaded), f"downloaded bytes are not a JPEG or PNG: {downloaded[:8]!r}"


def test_search_toggle_gates_websearch():
    """The admin search toggle is what core reads to refuse the websearch skill."""
    import telegram_channel as tc
    channel = tc.TelegramChannel()
    before = tc.is_search_disabled()
    try:
        tc._channel.search_disabled = True
        assert channel.is_tool_disabled("websearch")
        assert not channel.is_tool_disabled("send")
        tc._channel.search_disabled = False
        assert not channel.is_tool_disabled("websearch")
    finally:
        tc._channel.search_disabled = before


def test_gateway_routing_is_used_behind_a_proxy(monkeypatch):
    """With a proxy configured the bot talks to it, not to api.telegram.org.

    This is how the plugin runs under core's stock entrypoint: the token stays in
    nginx and the plugin sends a placeholder, so the routing is what has to be
    right rather than the credential.
    """
    import auth
    import telegram_channel as tc
    monkeypatch.setattr(auth, "get_proxy_url", lambda: "http://localhost:8080")
    bot = tc._build_bot(tc._PROXY_TOKEN)
    base = bot.session.api.base if hasattr(bot.session, "api") else ""
    assert "localhost:8080/telegram/" in str(base), base
    file_base = bot.session.api.file if hasattr(bot.session, "api") else ""
    assert "localhost:8080/telegram-file/" in str(file_base), file_base
