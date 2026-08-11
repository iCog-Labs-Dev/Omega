"""One communication channel shared by several agents.

The orchestration benchmark puts a main agent and two collaborators in a single
channel and scores how well they coordinate, so the transport has to broadcast
every message to everyone and keep an ordered record of who said what. This
module is both halves of that: an HTTP server the host runs, and the two client
calls each agent makes.

Every message is appended to a JSONL transcript, which is the only artifact the
scorer reads. Delivery is per-agent: a cursor remembers what an agent has already
been handed, and an agent never receives its own message. The bus deliberately
does not enforce addressing — an agent answering a message meant for someone else
is a channel-discipline failure the benchmark wants to measure, not prevent.

    bus = Bus(Path("transcript.jsonl"))
    server, _ = serve(bus, 9770)            # host side
    post(("127.0.0.1", 9770), "User", "@Main solve this")
    author, text = poll(("127.0.0.1", 9770), "Main")   # blocks until a message

`puppets` replaces one agent with a canned reply: any message addressing that
agent is answered immediately with fixed text, and no container is started for
it. That is how the benchmark injects a reproducible collaborator error.
"""

import argparse
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# How long a receive call parks before returning empty. The agent loop calls the
# LLM once per iteration whether or not input arrived, so blocking here is what
# keeps an idle agent from burning a call per second.
POLL_TIMEOUT = 30.0


class Bus:
    """The channel itself: an ordered message list plus per-agent cursors."""

    def __init__(self, transcript_path, puppets=None):
        self.transcript_path = Path(transcript_path)
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        self.puppets = dict(puppets or {})
        self.messages = []
        self.cursors = {}
        self._cond = threading.Condition()

    def say(self, agent, text):
        """Publish a message to the channel, answering for any puppet it addresses."""
        with self._cond:
            self._append(agent, text)
            for name, reply in self.puppets.items():
                if name != agent and f"@{name}" in text:
                    self._append(name, reply)
            self._cond.notify_all()

    def next_for(self, agent, timeout=POLL_TIMEOUT):
        """Oldest message this agent has not seen and did not write, or None on timeout."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                # seq is the 1-based position, so the cursor doubles as a slice index.
                for message in self.messages[self.cursors.get(agent, 0):]:
                    self.cursors[agent] = message["seq"]
                    if message["agent"] != agent:
                        return message
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _append(self, agent, text):
        """Record a message. The caller holds the lock.

        The file is written before the in-memory list grows, so a failed write
        leaves both empty rather than leaving delivery ahead of the transcript
        the scorer reads.
        """
        message = {"seq": len(self.messages) + 1, "ts": time.time(),
                   "agent": agent, "text": text}
        with self.transcript_path.open("a") as handle:
            handle.write(json.dumps(message) + "\n")
        self.messages.append(message)


class _Handler(BaseHTTPRequestHandler):
    """Three routes over one Bus. Set by serve() as a class attribute."""

    bus = None

    def do_POST(self):
        if self.path != "/say":
            return self._reply(404, {"error": "no such route"})
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        print(f"[bus] POST /say from {self.client_address} agent={payload['agent']!r}",
              flush=True)
        self.bus.say(payload["agent"], payload["text"])
        self._reply(200, {"ok": True})

    def do_GET(self):
        route, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if route == "/next":
            timeout = float(params.get("timeout", [POLL_TIMEOUT])[0])
            message = self.bus.next_for(params["agent"][0], timeout)
            return self._reply(200, message or {})
        if route == "/transcript":
            return self._reply(200, {"messages": self.bus.messages})
        self._reply(404, {"error": "no such route"})

    def _reply(self, status, body):
        """Answer one request, tolerating an agent that left while it was waiting.

        A trial ends by removing its containers, and a container parked in a long-poll
        dies with the request still open — so the reply lands on a closed socket. That is
        ordinary teardown, not a failure: without this the server logs a BrokenPipeError
        traceback per agent per trial, which makes a healthy sweep look broken.
        """
        encoded = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, *args):
        """Silence per-request logging; the transcript is the record."""


def serve(bus, port, host="0.0.0.0"):
    """Start the bus on a background thread. Returns (server, thread); call shutdown()."""
    handler = type("_BoundHandler", (_Handler,), {"bus": bus})
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def post(address, agent, text, timeout=10):
    """Publish one message as `agent`."""
    body = json.dumps({"agent": agent, "text": text}).encode()
    request = urllib.request.Request(f"http://{address[0]}:{address[1]}/say", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def poll(address, agent, timeout=POLL_TIMEOUT):
    """Wait for the next message on the channel. Returns (author, text) or None."""
    url = (f"http://{address[0]}:{address[1]}/next?"
           + urllib.parse.urlencode({"agent": agent, "timeout": timeout}))
    with urllib.request.urlopen(url, timeout=timeout + 10) as response:
        message = json.loads(response.read())
    return (message["agent"], message["text"]) if message else None


def transcript(address, timeout=10):
    """Every message so far, oldest first."""
    url = f"http://{address[0]}:{address[1]}/transcript"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())["messages"]


def main():
    parser = argparse.ArgumentParser(description="Run the benchmark message bus.")
    parser.add_argument("--port", type=int, default=9770)
    parser.add_argument("--transcript", default="transcript.jsonl")
    parser.add_argument("--puppet", action="append", default=[], metavar="NAME=FILE",
                        help="answer for NAME with the contents of FILE")
    args = parser.parse_args()

    puppets = {}
    for spec in args.puppet:
        name, _, path = spec.partition("=")
        puppets[name] = Path(path).read_text()

    bus = Bus(args.transcript, puppets)
    server, thread = serve(bus, args.port)
    print(f"bus on :{args.port}, transcript {args.transcript}", flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
