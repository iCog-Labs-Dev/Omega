"""
Mock test of tool and turn listeners in the running agent loop.

A metta call registers a tool listener that denies write-file, replaces the
argument of shell, holds read-file for a few seconds and lets the rest
through. Its after-hook and a turn listener write what they see to a file.
One answer then runs all four kinds of call, and the next LLM request shows
what the model got back for each. Last, the listeners are removed and a
write-file runs as it does without them.

Run:
    pytest mock/test_tool_listeners_mock.py -s
"""
import re
import subprocess
import time

from helpers import CONTAINER, Checker, dexec, make_prompt, wait_for_skill_call

SLOW_SECONDS = 5


def docker_logs():
    res = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True)
    return ((res.stdout or "") + (res.stderr or "")).split("\n")


def request_after(tag):
    lines = docker_logs()
    first = next((i for i, l in enumerate(lines) if "REQUEST:" in l and tag in l), None)
    if first is None:
        return None
    return next((l for l in lines[first + 1:] if "REQUEST:" in l), None)


def metta_string(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def register_listeners(run_id, record):
    """The metta expression that adds the test's listeners. Named functions are
    used rather than lambdas, because a lambda inside an evaluated string does
    not compile."""
    record = metta_string(record)
    return (
        "(progn"
        " (add-atom &self (= (hooktest-before $id $tool $args)"
        "   (case $tool"
        "     ((write-file (Deny \"hooktest says no\"))"
        f"      (shell (Replace (cons-atom shell (\"echo replaced-{run_id}\"))))"
        f"      (read-file (progn (sleep {SLOW_SECONDS}) Allow))"
        "      ($other Allow)))))"
        " (add-atom &self (= (hooktest-after $id $result)"
        f"   (append-file \"{record}\" (strings-concat (\"after \" $id \" \" $result (newline))))))"
        " (add-atom &self (= (hooktest-turn $event $turn)"
        f"   (append-file \"{record}\" (strings-concat (\"turn \" $event \" \" $turn (newline))))))"
        " (add-tool-listener hooktest hooktest-before hooktest-after)"
        " (add-turn-listener hooktest hooktest-turn))"
    )


REMOVE_LISTENERS = "(progn (remove-tool-listener hooktest) (remove-turn-listener hooktest))"


def run_prompt(c, llm, comm, run_id, label, task, answer):
    prompt = make_prompt(run_id, task)
    llm.set_answer(prompt, answer)
    if not comm.send_message(prompt):
        c.fail(label, "could not deliver the prompt within 60s")
    return prompt


def test_tool_listeners_mock(llm, comm):
    with Checker("Tool and turn listeners see, deny and replace calls (mock)") as c:
        run_id = c.run_id
        print(f"\n=== Omega: tool listeners (run-id {run_id}) ===", flush=True)
        workdir = f"/tmp/omega-hooks-{run_id}"
        record = f"{workdir}/record.txt"
        denied = f"{workdir}/denied.txt"
        slow = f"{workdir}/slow.txt"
        allowed = f"{workdir}/allowed.txt"
        dexec("mkdir", "-p", workdir)
        dexec("sh", "-c", f"echo slow-{run_id} > {slow}")

        try:
            c.step("register the listeners")
            run_prompt(c, llm, comm, f"{run_id}-setup", "Set up the tool listeners.", [
                ("metta", {"sexpression": register_listeners(run_id, record)}),
                ("send", {"content": f"listeners ready {run_id}-setup"}),
            ])
            if wait_for_skill_call(f"{run_id}-setup", "send", timeout=60) is None:
                c.fail("setup", "the agent did not finish the setup answer")
            c.ok("setup")

            c.step("run a denied, a replaced, a slow and an allowed call")
            started = time.time()
            run_prompt(c, llm, comm, f"{run_id}-calls", "Run the four calls.", [
                ("write-file", {"filename": denied, "content": "should not exist"}),
                ("shell", {"cmd": f"echo original-{run_id}"}),
                ("read-file", {"filename": slow}),
                ("send", {"content": f"calls done {run_id}-calls"}),
            ])
            if wait_for_skill_call(f"{run_id}-calls", "send", timeout=60 + SLOW_SECONDS) is None:
                c.fail("calls", "the agent did not reach the send after the listener calls")
            elapsed = time.time() - started
            c.ok("calls", f"{elapsed:.1f}s")

            c.step("read what the model got back")
            deadline = time.time() + 30
            request = None
            while request is None and time.time() < deadline:
                time.sleep(2)
                request = request_after(f"REQ-{run_id}-calls")
            if request is None:
                c.fail("next request", "no REQUEST line after the one carrying the calls")
            calls = re.findall(r"LLMToolCall\[id='([^']*)',name='([^']*)'", request)
            results = dict((callid, content) for content, callid in re.findall(
                r"LLMToolCallResponseMessage\[role='tool',content=(.*?),callid='([^']*)'\]", request))
            ids = dict((name, callid) for callid, name in calls)
            if sorted(ids) != ["read-file", "send", "shell", "write-file"]:
                c.fail("calls in request", f"expected the four calls, got {calls}")

            if "Refused: hooktest says no" not in results.get(ids["write-file"], ""):
                c.fail("deny", f"the write-file result is not the refusal: {results.get(ids['write-file'])}")
            if dexec("test", "-e", denied).returncode == 0:
                c.fail("deny", f"{denied} exists, so the denied call ran")
            c.ok("deny", results[ids["write-file"]])

            shell_result = results.get(ids["shell"], "")
            if f"replaced-{run_id}" not in shell_result or f"original-{run_id}" in shell_result:
                c.fail("replace", f"the shell result is not the replacement's: {shell_result}")
            c.ok("replace", shell_result)

            read_result = results.get(ids["read-file"], "")
            if f"slow-{run_id}" not in read_result:
                c.fail("slow listener", f"the held read-file did not return the file: {read_result}")
            c.ok("slow listener", read_result)

            c.step("check the after-hook and turn events")
            seen = dexec("cat", record).stdout
            for name, callid in ids.items():
                if f"after {callid} " not in seen:
                    c.fail("after-hook", f"no after line for {name} ({callid}) in {seen!r}")
            if not re.search(rf"after {re.escape(ids['write-file'])} ALERT_FAILED.*Refused", seen):
                c.fail("after-hook", f"the after line of the denied call has no refusal: {seen!r}")
            c.ok("after-hook")
            turns = re.findall(r"turn (start|end) (\d+)", seen)
            starts = [t for e, t in turns if e == "start"]
            if not any(("end", t) in turns for t in starts):
                c.fail("turn events", f"no turn has both start and end: {turns}")
            c.ok("turn events", f"{turns[:6]}")

            c.step("the agent still answers after the slow call")
            run_prompt(c, llm, comm, f"{run_id}-after", "Say you are still here.", [
                ("send", {"content": f"still here {run_id}-after"}),
            ])
            if wait_for_skill_call(f"{run_id}-after", "send", timeout=60) is None:
                c.fail("still answering", "the agent did not answer the next message")
            c.ok("still answering")
        finally:
            run_prompt(c, llm, comm, f"{run_id}-teardown", "Remove the tool listeners.", [
                ("metta", {"sexpression": REMOVE_LISTENERS}),
                ("write-file", {"filename": allowed, "content": "ran"}),
                ("send", {"content": f"listeners removed {run_id}-teardown"}),
            ])
            wait_for_skill_call(f"{run_id}-teardown", "send", timeout=60)

        c.step("without listeners, write-file runs again")
        if dexec("cat", allowed).stdout.strip() != "ran":
            c.fail("no listener", f"{allowed} was not written after the listeners were removed")
        c.ok("no listener")
        dexec("rm", "-rf", workdir)
        c.done()
