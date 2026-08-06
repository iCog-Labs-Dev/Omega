"""Host-side checks for the benchmark bus. No Docker, no LLM: `pytest bench/test_bus.py`."""

import json
import threading

import bus


def test_everyone_but_the_author_receives_a_message(tmp_path):
    channel = bus.Bus(tmp_path / "t.jsonl")
    channel.say("Main", "@Agent-A start")

    assert channel.next_for("Agent-A", timeout=0) == {**channel.messages[0]}
    assert channel.next_for("Agent-B", timeout=0)["text"] == "@Agent-A start"
    assert channel.next_for("Main", timeout=0) is None


def test_a_message_is_delivered_once(tmp_path):
    channel = bus.Bus(tmp_path / "t.jsonl")
    channel.say("Main", "first")
    channel.say("Main", "second")

    assert channel.next_for("Agent-A", timeout=0)["text"] == "first"
    assert channel.next_for("Agent-A", timeout=0)["text"] == "second"
    assert channel.next_for("Agent-A", timeout=0) is None


def test_a_waiting_agent_wakes_on_a_late_message(tmp_path):
    channel = bus.Bus(tmp_path / "t.jsonl")
    threading.Timer(0.2, channel.say, ("Main", "late")).start()

    assert channel.next_for("Agent-A", timeout=5)["text"] == "late"


def test_a_puppet_answers_without_ever_connecting(tmp_path):
    channel = bus.Bus(tmp_path / "t.jsonl", puppets={"Agent-A": "[RESULT] wrong on purpose"})
    channel.say("Main", "@Agent-A compute the optimum")

    assert channel.next_for("Main", timeout=0)["agent"] == "Agent-A"
    assert channel.next_for("Main", timeout=0) is None


def test_a_puppet_ignores_messages_addressed_elsewhere(tmp_path):
    channel = bus.Bus(tmp_path / "t.jsonl", puppets={"Agent-A": "canned"})
    channel.say("Main", "@Agent-B your turn")

    assert [m["agent"] for m in channel.messages] == ["Main"]


def test_the_transcript_is_ordered_jsonl(tmp_path):
    path = tmp_path / "t.jsonl"
    channel = bus.Bus(path)
    channel.say("User", "@Main the task")
    channel.say("Main", "@Agent-A a subtask")

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["seq"], r["agent"]) for r in records] == [(1, "User"), (2, "Main")]


def test_the_transcript_directory_is_created(tmp_path):
    channel = bus.Bus(tmp_path / "runs" / "r1" / "t.jsonl")
    channel.say("User", "hello")

    assert (tmp_path / "runs" / "r1" / "t.jsonl").exists()


def test_the_http_layer_carries_a_round_trip(tmp_path):
    channel = bus.Bus(tmp_path / "t.jsonl")
    server, _ = bus.serve(channel, 0, host="127.0.0.1")
    address = ("127.0.0.1", server.server_address[1])
    try:
        bus.post(address, "User", "@Main the task")
        assert bus.poll(address, "Main", timeout=5) == ("User", "@Main the task")
        assert bus.poll(address, "Main", timeout=0.5) is None
        assert [m["text"] for m in bus.transcript(address)] == ["@Main the task"]
    finally:
        server.shutdown()
