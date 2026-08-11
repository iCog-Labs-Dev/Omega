"""Run one benchmark trial: agents in containers, one shared channel, one transcript.

A trial is a task plus a configuration plus a trial number. The runner renders a role
prompt per agent, starts a container per agent against a private bus, posts the task to
the channel, and watches until the main agent publishes a final answer or a limit is
reached. What it leaves behind — the transcript, each container's log, and the run
metadata — is everything the scorer needs.

    bench/runner.py --tasks qr1 --configs framed,plain,solo --trials 1
    bench/runner.py --tasks qr1 --perturb      # inject the task's scripted bad result
    bench/runner.py --full                     # every task, every config, three trials

Each trial gets its own container names and its own bus port. Fixed names let a leftover
run adopt the next run's containers and post into its bus, which is indistinguishable
from a misbehaving agent.
"""

import argparse
import json
import random
import re
import shutil
import socket
import string
import subprocess
import sys
import time
from pathlib import Path

import yaml

import bus
import clean_log

HERE = Path(__file__).resolve().parent
CONTAINER_MEMORY_DIR = "/PeTTa/repos/OmegaClaw-Core/memory"
CONTAINER_POLICY = "/PeTTa/repos/OmegaClaw-Core/profile/policy.yaml"

# Collaborators are renamed every trial so nothing can route by a fixed identity.
NAME_POOL = ["Agent-A", "Agent-B", "Nova", "Pike", "Wren", "Kade", "Juno", "Reva"]
MAIN_NAME = "Main"

# Configurations differ only in the role prompt the main agent gets, and in whether
# collaborators exist at all.
CONFIGS = {
    "framed": {"main": ["main_plain.txt", "frame.txt"], "collaborators": 2},
    "plain": {"main": ["main_plain.txt"], "collaborators": 2},
    "solo": {"main": ["solo.txt"], "collaborators": 0},
}


def load_task(task_id):
    return yaml.safe_load((HERE / "tasks" / f"{task_id}.yaml").read_text())


def free_port():
    with socket.socket() as probe:
        probe.bind(("", 0))
        return probe.getsockname()[1]


def render_role(files, **fields):
    """Concatenate role templates and fill in the trial's names and limits."""
    text = "".join((HERE / "roles" / name).read_text() for name in files)
    return string.Template(text).safe_substitute(**fields)


def plan_trial(task, config_name, trial, budget, perturb):
    """Decide the agents for one trial: their names, roles, and who is a puppet.

    Names are drawn from a seed built from the task and trial only, deliberately not
    the configuration: every configuration of one trial then sees the same collaborator
    names, so a framed-versus-plain comparison differs by the frame and nothing else,
    while separate trials still shuffle names to expose identity-specific routing.
    """
    config = CONFIGS[config_name]
    rng = random.Random(f"{task['id']}-{trial}")
    names = rng.sample(NAME_POOL, config["collaborators"])
    slots = {f"collaborator_{i + 1}": name for i, name in enumerate(names)}

    puppet = None
    if perturb and task.get("perturbation"):
        puppet = slots.get(task["perturbation"]["puppet"])

    fields = {
        "NAME": MAIN_NAME,
        "MAIN": MAIN_NAME,
        "PEERS": " and ".join(names) if names else "nobody",
        "PEER_ONE": names[0] if names else "",
        "PEER_TWO": names[1] if len(names) > 1 else "",
        "BUDGET": budget,
    }
    agents = [{"name": MAIN_NAME, "role": render_role(config["main"], **fields),
               "puppet": False}]
    for index, name in enumerate(names):
        letter = chr(ord("A") + index)
        agents.append({
            "name": name,
            "role": render_role(["collaborator.txt"], **{
                **fields, "NAME": name,
                "RESULT_PREFIX": f"{task['id'].upper()}-{letter}",
            }),
            "puppet": name == puppet,
        })
    return agents, puppet


def write_agent_dirs(trial_dir, agents):
    """One memory directory per agent: its role prompt, its history, its vector store.

    A fresh directory per trial is also how memory is reset between trials.
    """
    for agent in agents:
        memory = trial_dir / "agents" / agent["name"] / "memory"
        (memory / "chroma_db").mkdir(parents=True, exist_ok=True)
        (memory / "prompt.txt").write_text(agent["role"])
        (memory / "history.metta").write_text("")
        for path in [memory, *memory.rglob("*")]:
            path.chmod(0o777)


def launch(container, agent_name, memory_dir, port, args):
    """Start one agent. Flags mirror scripts/omegaclaw: without -it the container's
    nginx cannot open /dev/stderr and the agent dies before the loop starts."""
    subprocess.run([
        "docker", "run", "-d", "-it", "--name", container,
        "--security-opt", "no-new-privileges:true", "--init",
        "--tmpfs", "/tmp:size=64m,mode=1777",
        "--tmpfs", "/var/tmp:size=64m,mode=1777",
        "--tmpfs", "/run:size=16m,mode=755",
        "--env-file", args.env_file,
        "-v", f"{memory_dir}:{CONTAINER_MEMORY_DIR}",
        args.image,
        "commchannel=bench", f"agent={agent_name}", f"bus={args.bus_host}:{port}",
        f"provider={args.provider}", "embeddingprovider=Local",
        f"securityPolicyPath={CONTAINER_POLICY}", "memoryDirectory=$MEMORY_DIR",
        *([f"model={args.model}"] if args.model else []),
    ], check=True, capture_output=True)


def container_running(container):
    result = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                            capture_output=True, text=True)
    return result.stdout.strip() == "true"


def wait_for_loop(containers, timeout=180):
    """Wait until every container has built its first prompt, or report the dead one.

    The match needs the byte count: the bare string CHARS_SENT: also appears in the
    MeTTa source dump at startup, long before any prompt is built.
    """
    deadline = time.monotonic() + timeout
    for container in containers:
        while True:
            logs = subprocess.run(["docker", "logs", container],
                                  capture_output=True, text=True)
            if re.search(r"CHARS_SENT: \d+", logs.stdout + logs.stderr):
                break
            if not container_running(container):
                return f"{container} exited before its loop started"
            if time.monotonic() > deadline:
                return f"{container} did not start its loop within {timeout}s"
            time.sleep(2)
    return None


def final_answer(channel):
    """The main agent's first message that opens with FINAL ANSWER, if any."""
    for message in channel.messages:
        if message["agent"] == MAIN_NAME and message["text"].strip().upper().startswith("FINAL ANSWER"):
            return message
    return None


def final_answer_text(channel):
    """The published answer, including any continuation the main agent adds after it.

    Agents split long answers across messages when the deliverable list is long. The
    scorer needs the whole thing, so everything the main agent says from its first
    FINAL ANSWER onward is joined. Whether a continuation is really a second, competing
    answer is a judgement call and stays with the scorer.
    """
    first = final_answer(channel)
    if first is None:
        return None
    tail = [m["text"] for m in channel.messages
            if m["agent"] == MAIN_NAME and m["seq"] >= first["seq"]]
    return "\n\n".join(tail)


def watch(channel, containers, args):
    """Block until the main agent finishes, a limit is hit, or an agent dies.

    Finishing is not instant: after the first FINAL ANSWER the watch keeps running for a
    grace period, so a continuation message is captured rather than cut off mid-answer.
    """
    deadline = time.monotonic() + args.wall_clock
    finished_at = None
    while True:
        if final_answer(channel):
            finished_at = finished_at or time.monotonic()
            if time.monotonic() - finished_at >= args.final_grace:
                return "final_answer"
        if len(channel.messages) >= args.max_messages:
            return "message_cap"
        if time.monotonic() > deadline:
            return "wall_clock"
        dead = [c for c in containers if not container_running(c)]
        if dead:
            return f"agent_died:{','.join(dead)}"
        time.sleep(3)


def save_logs(trial_dir, agents, containers):
    for agent, container in zip(agents, containers):
        logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
        raw = logs.stdout + logs.stderr
        path = trial_dir / "agents" / agent["name"] / "docker.log"
        path.write_text(raw)
        path.with_suffix(".clean.log").write_text(clean_log.clean(raw))


def trial_done(trial_dir):
    """A trial counts as done only if its run.json is a complete, valid record.

    A run killed mid-trial can leave a trial_dir with no run.json, or one that's empty
    or truncated (the process died mid-write) — either way json.loads fails and the
    trial is treated as not done, same as one that was never started. run_trial wipes
    and redoes it.
    """
    try:
        json.loads((trial_dir / "run.json").read_text())
        return True
    except (OSError, json.JSONDecodeError):
        return False


def run_trial(task, config_name, trial, args):
    trial_dir = args.out / task["id"] / config_name / f"trial-{trial}"
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    trial_dir.mkdir(parents=True)

    agents, puppet = plan_trial(task, config_name, trial, args.max_messages, args.perturb)
    write_agent_dirs(trial_dir, agents)

    puppets = {puppet: task["perturbation"]["reply"]} if puppet else {}
    channel = bus.Bus(trial_dir / "transcript.jsonl", puppets)
    port = free_port()
    server, _ = bus.serve(channel, port)

    run_id = f"bench-{task['id']}-{config_name}-t{trial}-{port}"
    live = [a for a in agents if not a["puppet"]]
    containers = [f"{run_id}-{a['name'].lower()}" for a in live]

    started = time.time()
    stop_reason = "setup_failed"
    try:
        # The task is posted before any agent connects. An agent's first receive then
        # returns it straight away; posting later costs the main agent a turn spent
        # answering an empty channel, because the loop calls the model whether or not
        # input arrived.
        prompt = task["prompt"]
        if config_name != "solo":
            prompt += "\n\nYou must use both collaborator agents."
        channel.say("User", f"@{MAIN_NAME} {prompt}")

        for agent, container in zip(live, containers):
            launch(container, agent["name"],
                   trial_dir / "agents" / agent["name"] / "memory", port, args)
        stop_reason = wait_for_loop(containers) or watch(channel, containers, args)
    finally:
        save_logs(trial_dir, live, containers)
        subprocess.run(["docker", "rm", "-f", *containers], capture_output=True)
        server.shutdown()

    answer = final_answer(channel)
    record = {
        "task": task["id"], "config": config_name, "trial": trial,
        "agents": [{"name": a["name"], "puppet": a["puppet"]} for a in agents],
        "puppet_reply": puppets.get(puppet),
        "stop_reason": stop_reason,
        "final_answer": final_answer_text(channel),
        "messages": channel.messages,
        "message_count": len(channel.messages),
        "duration_s": round(time.time() - started, 1),
        "limits": {"max_messages": args.max_messages, "wall_clock": args.wall_clock},
        "provider": args.provider, "model": args.model, "image": args.image,
    }
    (trial_dir / "run.json").write_text(json.dumps(record, indent=2))
    print(f"  {task['id']}/{config_name}/trial-{trial}: {stop_reason}, "
          f"{len(channel.messages)} messages, {record['duration_s']}s")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", default="qr1", help="comma-separated task ids")
    parser.add_argument("--configs", default="framed,plain,solo")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--full", action="store_true",
                        help="every task, every config, three trials")
    parser.add_argument("--perturb", action="store_true",
                        help="inject the task's scripted faulty collaborator result")
    parser.add_argument("--image", default="omegaclaw:bench")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--provider", default="Anthropic")
    parser.add_argument("--model", default=None,
                        help="overrides the provider's config.yaml default model")
    parser.add_argument("--bus-host", default="172.17.0.1",
                        help="host address containers reach on the docker bridge")
    parser.add_argument("--max-messages", type=int, default=24)
    parser.add_argument("--wall-clock", type=int, default=900, help="seconds per trial")
    parser.add_argument("--final-grace", type=int, default=30,
                        help="seconds to keep listening after the first FINAL ANSWER")
    parser.add_argument("--out", type=Path, default=HERE / "runs")
    args = parser.parse_args()
    args.out = args.out.resolve()

    if args.full:
        task_ids = sorted(p.stem for p in (HERE / "tasks").glob("*.yaml"))
        configs, trials = list(CONFIGS), 3
    else:
        task_ids = args.tasks.split(",")
        configs = args.configs.split(",")
        trials = args.trials

    unknown = set(configs) - set(CONFIGS)
    if unknown:
        sys.exit(f"unknown configs: {', '.join(sorted(unknown))}")

    print(f"{len(task_ids)} task(s) x {len(configs)} config(s) x {trials} trial(s)")
    for task_id in task_ids:
        task = load_task(task_id)
        for config_name in configs:
            for trial in range(1, trials + 1):
                trial_dir = args.out / task_id / config_name / f"trial-{trial}"
                if trial_done(trial_dir):
                    print(f"  {task_id}/{config_name}/trial-{trial}: already done, skipping")
                    continue
                run_trial(task, config_name, trial, args)


if __name__ == "__main__":
    main()
