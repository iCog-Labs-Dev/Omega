"""The reply gate end to end: real core, real plugin, a scripted model.

QA tested the gate on singnet core with a stub model and found answers it
refused: one after a short status note, and the second of two questions sent
close together. The unit tests in test_reply_gate.py drive the gate directly.
These drive it the way production does: core runs its own loop in a container
with this plugin loaded, the model's replies come from core's mock provider
(provider=Test), and Telegram is a fake Bot API server in this process, so what
the bot posts can be read back.

    core loop -> mock model (scripted here) -> send -> plugin -> fake Telegram

The suite is opt-in. It needs Docker, a core checkout for the mock model's
controller, and a container started against this process:

    git -C <omega> archive cbots/telegram-core | tar -x -C /tmp/core
    printf 'TG_BOT_TOKEN=1:e2e\\nTEST_SERVER_IP=172.17.0.1\\n' > /tmp/e2e.env
    ./run-plugin --core-dir /tmp/core --image omega-telegram:gate-e2e \\
        --container omega-gate-e2e --env-file /tmp/e2e.env --replace \\
        --arg provider=Test --arg embeddingprovider=Local \\
        --arg TG_CHAT_ID=-1001000000001 \\
        --arg GATEWAY_URL=http://172.17.0.1:9800
    TG_GATE_E2E=1 OMEGA_CORE_DIR=/tmp/core OMEGA_CONTAINER=omega-gate-e2e \\
        pytest telegram/tests/test_reply_gate_e2e.py -v -s

GATEWAY_URL sends the plugin's Telegram and moderation calls here instead of
through the container's proxy. Start pytest within a minute or so of the
container: the mock model connects to the controller here and retries until it
can.

The scenarios run in order and share one chat, because a message's leftover
posts would let a later scenario's post through. Each one that expects a
refusal starts with the chat closed.
"""
import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TG_GATE_E2E") != "1",
    reason="opt-in: set TG_GATE_E2E=1 with a container running (see the module docstring)")

CHAT = -1001000000001
ADMIN = 7001
BOT = {"id": 9000, "is_bot": True, "first_name": "Omega", "username": "omega_e2e_bot"}
USER = {"id": 5001, "is_bot": False, "first_name": "QA", "username": "qa_user"}
PORT = 9800
CONTAINER = os.environ.get("OMEGA_CONTAINER", "omega-gate-e2e")

# What core hands the model on a turn with no new message.
IDLE = ("[NO NEW USER INPUT. CONTINUE AUTONOMOUS WORK. DO NOT REPEAT THE PREVIOUS "
        "RESPONSE. ONLY USE send FOR GENUINELY NEW INFORMATION OR WHEN USER INPUT IS NEEDED.]")
PUBLIC_NOTICE = "Seems there is a problem, please try again later."


class FakeTelegram:
    """Just enough of the Bot API for the plugin: hands out queued updates to
    getUpdates and records every message the bot sends."""

    def __init__(self):
        self._lock = threading.Condition()
        self._updates = []
        self._next_update = 1
        self._next_message = 100
        self.sent = []  # (chat_id, text)
        self.calls = []

    def say(self, text):
        """A user posts `text` in the group, tagging the bot."""
        with self._lock:
            self._next_message += 1
            self._updates.append({"update_id": self._next_update, "message": {
                "message_id": self._next_message, "date": int(time.time()),
                "chat": {"id": CHAT, "type": "supergroup", "title": "e2e"},
                "from": USER, "text": f"@{BOT['username']} {text}"}})
            self._next_update += 1
            self._lock.notify_all()

    def texts(self, chat_id):
        with self._lock:
            return [text for chat, text in self.sent if chat == chat_id]

    def answer(self, method, params):
        self.calls.append(method)
        if method == "getMe":
            return BOT
        if method == "getChatAdministrators":
            return [{"status": "creator", "user": {"id": ADMIN, "is_bot": False,
                                                    "first_name": "Admin"},
                     "is_anonymous": False}]
        if method == "getChat":
            return {"id": int(params.get("chat_id", CHAT)), "type": "supergroup", "title": "e2e"}
        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            deadline = time.time() + min(float(params.get("timeout") or 0), 5)
            with self._lock:
                while True:
                    ready = [u for u in self._updates if u["update_id"] >= offset]
                    if ready or time.time() >= deadline:
                        return ready
                    self._lock.wait(deadline - time.time())
        if method.startswith("send") and method != "sendChatAction":
            chat_id = int(params["chat_id"])
            text = params.get("text") or params.get("caption") or f"<{method}>"
            if params.get("parse_mode") == "MarkdownV2":
                # What the user sees: Telegram drops the escaping backslashes.
                text = re.sub(r"\\(.)", r"\1", text)
            with self._lock:
                self.sent.append((chat_id, text))
                self._next_message += 1
                message_id = self._next_message
            return {"message_id": message_id, "date": int(time.time()), "from": BOT,
                    "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "supergroup"},
                    "text": text}
        return True


def _params(handler):
    """The request's parameters, whether sent as a form, multipart or JSON."""
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length) if length else b""
    kind = handler.headers.get("Content-Type", "")
    params = {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items()}
    if kind.startswith("application/json") and body:
        params.update(json.loads(body))
    elif kind.startswith("multipart/form-data"):
        message = BytesParser(policy=HTTP).parsebytes(
            f"Content-Type: {kind}\r\n\r\n".encode() + body)
        for part in message.iter_parts():
            if part.get_filename() is None:
                params[part.get_param("name", header="content-disposition")] = \
                    part.get_content().strip() if isinstance(part.get_content(), str) else ""
    elif body:
        params.update({k: v[0] for k, v in parse_qs(body.decode()).items()})
    return params


def _serve(telegram):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            path = urlparse(self.path).path
            params = _params(self)
            if path.startswith("/telegram/"):
                reply = {"ok": True, "result": telegram.answer(path.rsplit("/", 1)[1], params)}
            elif path.endswith("/moderations"):
                # Nothing a user says here is flagged.
                scores = dict.fromkeys(("violence", "harassment", "hate", "self-harm",
                                        "sexual/minors"), 0.0)
                reply = {"id": "modr-e2e", "model": "omni-moderation-latest", "results": [
                    {"flagged": False, "categories": dict.fromkeys(scores, False),
                     "category_scores": scores}]}
            else:
                self.send_error(404)
                return
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _core_dir():
    core = os.environ.get("OMEGA_CORE_DIR")
    if not core:
        pytest.skip("OMEGA_CORE_DIR is not set; it holds the mock model's controller")
    return core


@pytest.fixture(scope="module")
def telegram():
    fake = FakeTelegram()
    server = _serve(fake)
    try:
        yield fake
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def model():
    core = _core_dir()
    for path in (core, os.path.join(core, "providers"), os.path.join(core, "Autotests", "mock")):
        sys.path.insert(0, path)
    from llm import LlmMockController, LLM_MOCK_PORT
    controller = LlmMockController(("0.0.0.0", LLM_MOCK_PORT))
    try:
        yield controller
    finally:
        controller.stop(5)


@pytest.fixture(scope="module")
def bot(telegram, model):
    """The running bot, once it is polling this fake Telegram."""
    deadline = time.time() + 300
    while "getUpdates" not in telegram.calls:
        if time.time() > deadline:
            pytest.fail(f"container {CONTAINER} never polled the fake Telegram on port {PORT}")
        time.sleep(1)
    if not model.ping(120):
        pytest.fail("the mock model in the container never connected")
    return telegram


@pytest.fixture
def run_id(model):
    rid = uuid.uuid4().hex[:6]
    yield rid
    model.clear_answer(IDLE)


def _token_limit_text():
    """Core's own token-limit notice, read from its provider library."""
    path = os.path.join(_core_dir(), "providers", "lib_llm_ext.py")
    with open(path) as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == \
                "LLM_EMPTY_RESPONSE_MESSAGE":
            return ast.literal_eval(node.value)
    pytest.skip("this core has no token-limit notice")


def _wait_for(check, timeout=60, what="it"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.5)
    pytest.fail(f"timed out waiting for {what}")


def _logs_since(since):
    result = subprocess.run(["docker", "logs", "--since", f"{since:.3f}", CONTAINER],
                            capture_output=True, text=True, errors="replace")
    return result.stdout + result.stderr


def _ask(bot, model, question, calls):
    """Post `question` and have the model answer it with `calls`."""
    model.set_answer(f"@{BOT['username']} {question}", calls)
    bot.say(question)


def _send(text):
    return [("send", {"content": text})]


def _delivered(bot, text):
    return lambda: any(text in sent for sent in bot.texts(CHAT))


def _refused_without_delivery(bot, model, text, since):
    """Have the model post `text` on idle turns and check that the plugin
    refused it and nothing reached the chat."""
    model.set_answer(IDLE, _send(text))
    _wait_for(lambda: "SEND_REFUSED_NO_MESSAGE_TO_ANSWER" in _logs_since(since)
              or text in "".join(bot.texts(CHAT)), what=f"the model to post {text!r}")
    time.sleep(3)  # a few more idle turns
    model.clear_answer(IDLE)
    assert not any(text in sent for sent in bot.texts(CHAT))


def test_the_answer_follows_a_status_note_and_research_stays_out(bot, model, run_id):
    note, answer, research = (f"Starting now, looking it up {run_id}",
                              f"About 130 million {run_id}",
                              f"#462 Data Pruning verified {run_id}")
    _ask(bot, model, f"population of Ethiopia {run_id}", _send(note))
    _wait_for(_delivered(bot, note), what="the status note")
    model.set_answer(IDLE, _send(answer))
    _wait_for(_delivered(bot, answer), what="the answer after the note")
    since = time.time()
    _refused_without_delivery(bot, model, research, since)
    assert [t for t in bot.texts(CHAT) if run_id in t] == [note, answer]


def test_a_failure_on_an_idle_turn_reaches_admins_only(bot, model, run_id):
    reply, follow_up = f"Hello {run_id}", f"One more thing {run_id}"
    _ask(bot, model, f"hi {run_id}", _send(reply))
    _wait_for(_delivered(bot, reply), what="the reply")
    model.set_answer(IDLE, _send(follow_up))
    _wait_for(_delivered(bot, follow_up), what="the follow-up")
    # The chat is closed now: the message had both its posts.
    before = len(bot.texts(CHAT))
    model.set_answer(IDLE, _send(_token_limit_text()))
    _wait_for(lambda: any("maxOutputToken" in t for t in bot.texts(ADMIN)),
              what="the admin alert")
    time.sleep(3)
    assert bot.texts(CHAT)[before:] == []
    # Core drops a send identical to its last one, delivered or not, so the
    # next scenario's notice would never reach the plugin. Leave core with a
    # different last send.
    _refused_without_delivery(bot, model, f"Still researching {run_id}", time.time())


def test_a_waiting_user_hears_only_that_something_went_wrong(bot, model, run_id):
    answer = f"I can search the web {run_id}"
    _ask(bot, model, f"what can you do {run_id}",
         _send(_token_limit_text()) + _send(answer))
    _wait_for(_delivered(bot, answer), what="the answer after the notice")
    mine = bot.texts(CHAT)[-2:]
    assert mine == [PUBLIC_NOTICE, answer]
    assert not any("maxOutputToken" in t for t in bot.texts(CHAT))


def test_each_of_two_questions_gets_its_answer(bot, model, run_id):
    first, second = f"About 130 million {run_id}", f"About 56 million {run_id}"
    since = time.time()
    # The first question's turn only looks something up, so its answer goes
    # out after the second question has arrived.
    _ask(bot, model, f"population of Ethiopia {run_id}", [("nop", {})])
    _wait_for(lambda: "'nop'" in _logs_since(since), what="the lookup turn")
    _ask(bot, model, f"and of Kenya {run_id}", _send(first))
    _wait_for(_delivered(bot, first), what="the first answer")
    model.set_answer(IDLE, _send(second))
    _wait_for(_delivered(bot, second), what="the second answer")
