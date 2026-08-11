"""
Mock tests for the openclaw plugin's delegate-task-to-openclaw-agent skill.

The LLM and comm channel are mocked and deterministic, but the skill itself
runs for real: it makes an actual HTTP call to the OpenClaw Gateway - in
Docker via the local Nginx proxy (see plugins/openclaw/README.md), which is
why the container must be started with the Gateway URL known at launch (see
Autotests/mock/README.md, "5a. OpenClaw plugin"). That Gateway (mock or
live) is started and configured by the tester ahead of time - these tests do
not manage it.

Run:
    pytest test_openclaw_delegate_mock.py -s
"""
import json

from helpers import Checker, dexec, make_prompt, wait_for_skill_call


def _read_json(path):
    raw = dexec("cat", path).stdout.strip()
    return json.loads(raw)


def test_delegate_isolated_and_success_mock(llm, comm):
    with Checker("delegate-task-to-openclaw-agent mock") as c:
        print(f"\n=== OmegaClaw: openclaw delegate mock (run-id {c.run_id}) ===", flush=True)

        ####################################
        # Phase 1: check isolated skill call
        ####################################

        c.step("send prompt to check isolated skill invocation")
        prompt = make_prompt(c.run_id, "Delegate a task to OpenClaw.")
        llm.set_answer(
            request=prompt,
            response=(
                f'(send "Delegating task {c.run_id}")\n'
                '(delegate-task-to-openclaw-agent "Reply with exactly: unused")'
            ),
        )
        if not comm.send_message(prompt):
            c.fail("comm", "could not deliver prompt within timeout")

        c.step("verify send message does not absorb the skill")
        send_arg = wait_for_skill_call(c.run_id, "send", timeout=30, arg_substr="Delegating task")
        if not send_arg:
            c.fail("send", "Agent did not respond to first prompt.")
        if "delegate-task-to-openclaw-agent" in send_arg:
            c.fail("parser", "Bug regression: delegate call was absorbed into the send message.")
        c.ok("parser", "delegate-task-to-openclaw-agent was correctly parsed as a separate command.")

        ##########################################
        # Phase 2: real delegation, verify content
        ##########################################

        out_path = f"/tmp/openclaw_out_{c.run_id}.json"
        echo_marker = f"PONG-{c.run_id}"

        c.step("send prompt that writes the skill result straight to a file")
        prompt = make_prompt(c.run_id + 1, "Delegate a task and save the raw result.")
        llm.set_answer(
            request=prompt,
            response=(
                f'(metta (write-file "{out_path}" '
                f'(delegate-task-to-openclaw-agent "Reply with exactly: {echo_marker}")))\n'
                f'(send "Delegation saved {c.run_id}")'
            ),
        )
        if not comm.send_message(prompt):
            c.fail("comm", "could not deliver prompt within timeout")
        c.ok("comm", f"run-id={c.run_id}")

        c.step("wait for the agent to complete the delegation")
        send_arg = wait_for_skill_call(c.run_id, "send", timeout=600, arg_substr="Delegation saved")
        if not send_arg:
            c.fail("send", "Agent did not respond. Delegation might have failed or hung.")
        c.ok("send", "Agent successfully completed the delegation.")

        c.step("read and parse the skill's JSON result from the container")
        try:
            result = _read_json(out_path)
        except json.JSONDecodeError as e:
            c.fail("json", f"Failed to parse skill result: {e}")
        c.ok("json_parse", "delegate-task-to-openclaw-agent result successfully parsed")

        c.step("verify the result reports success with the expected content")
        if result.get("status") != "ok":
            c.fail("status", f"expected status 'ok', got: {result}")
        if not result.get("responseId"):
            c.fail("responseId", f"responseId missing from result: {result}")
        if echo_marker not in result.get("reply", ""):
            c.fail("reply", f"expected reply to contain {echo_marker!r}, got: {result.get('reply')!r}")
        c.ok("result", f"reply={result['reply'][:80]!r}")

        c.done()


def test_delegate_empty_message_mock(llm, comm):
    with Checker("delegate-task-to-openclaw-agent empty message mock") as c:
        print(f"\n=== OmegaClaw: openclaw delegate empty mock (run-id {c.run_id}) ===", flush=True)

        out_path = f"/tmp/openclaw_empty_{c.run_id}.json"

        c.step("send prompt that delegates an empty message")
        prompt = make_prompt(c.run_id, "Delegate an empty task and save the raw result.")
        llm.set_answer(
            request=prompt,
            response=(
                f'(metta (write-file "{out_path}" (delegate-task-to-openclaw-agent "")))\n'
                f'(send "Empty delegation checked {c.run_id}")'
            ),
        )
        if not comm.send_message(prompt):
            c.fail("comm", "could not deliver prompt within timeout")

        c.step("wait for the agent to complete without crashing")
        send_arg = wait_for_skill_call(c.run_id, "send", timeout=30, arg_substr="Empty delegation checked")
        if not send_arg:
            c.fail("send", "Agent did not respond (might have crashed on an empty message).")
        c.ok("send", "Agent survived an empty delegation.")

        c.step("verify the skill rejected the empty message without a network call")
        try:
            result = _read_json(out_path)
        except json.JSONDecodeError as e:
            c.fail("json", f"Failed to parse skill result: {e}")
        if result.get("status") != "error" or result.get("type") != "invalid_input":
            c.fail("result", f"expected an invalid_input error, got: {result}")
        c.ok("result", f"{result}")

        c.done()


def test_delegate_new_session_per_call_mock(llm, comm):
    with Checker("delegate-task-to-openclaw-agent new session per call mock") as c:
        print(f"\n=== OmegaClaw: openclaw delegate session isolation mock (run-id {c.run_id}) ===", flush=True)

        first_path = f"/tmp/openclaw_session1_{c.run_id}.json"
        second_path = f"/tmp/openclaw_session2_{c.run_id}.json"

        c.step("send prompt that delegates two independent tasks")
        prompt = make_prompt(c.run_id, "Delegate two independent tasks and save both raw results.")
        llm.set_answer(
            request=prompt,
            response=(
                f'(metta (write-file "{first_path}" '
                f'(delegate-task-to-openclaw-agent "Reply with exactly: FIRST-{c.run_id}")))\n'
                f'(metta (write-file "{second_path}" '
                f'(delegate-task-to-openclaw-agent "Reply with exactly: SECOND-{c.run_id}")))\n'
                f'(send "Both delegations saved {c.run_id}")'
            ),
        )
        if not comm.send_message(prompt):
            c.fail("comm", "could not deliver prompt within timeout")

        c.step("wait for the agent to complete both delegations")
        send_arg = wait_for_skill_call(c.run_id, "send", timeout=600, arg_substr="Both delegations saved")
        if not send_arg:
            c.fail("send", "Agent did not respond. One of the delegations might have hung.")
        c.ok("send", "Agent completed both delegations.")

        c.step("read and parse both JSON results")
        try:
            first = _read_json(first_path)
            second = _read_json(second_path)
        except json.JSONDecodeError as e:
            c.fail("json", f"Failed to parse a skill result: {e}")
        c.ok("json_parse", "both results successfully parsed")

        c.step("verify both calls succeeded with different session response ids")
        if first.get("status") != "ok" or second.get("status") != "ok":
            c.fail("status", f"expected both calls to succeed, got: {first}, {second}")
        first_id, second_id = first.get("responseId"), second.get("responseId")
        if not first_id or not second_id:
            c.fail("responseId", f"missing responseId: {first}, {second}")
        if first_id == second_id:
            c.fail("session isolation", f"both calls returned the same responseId: {first_id!r}")
        c.ok("session isolation", f"first={first_id!r} second={second_id!r}")

        c.done()
