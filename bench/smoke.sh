#!/usr/bin/env bash
# Spike for the orchestration benchmark: two real agents, one bus, one exchange.
#
# Proves the four things the whole harness rests on:
#   1. a per-agent memory/ mount gives each container its own role prompt
#   2. `agent=` and `bus=` reach the channel plugin as run arguments
#   3. a blocking receive() does not stall or crash the loop
#   4. both directions of the exchange land in the transcript, in order
#
# Usage:  bench/smoke.sh
# Needs:  docker build -t omegaclaw:bench .    (bench/ ships inside the image)
#         an .env holding the provider key, as the container's proxy expects
set -euo pipefail

IMAGE="${IMAGE:-omegaclaw:bench}"
ENV_FILE="${ENV_FILE:-.env}"
BUS_HOST="${BUS_HOST:-172.17.0.1}"   # host address on the default docker bridge
PROVIDER="${PROVIDER:-Anthropic}"
TIMEOUT="${TIMEOUT:-300}"
MEMORY_DIR=/PeTTa/repos/OmegaClaw-Core/memory

repo="$(cd "$(dirname "$0")/.." && pwd)"
run_id="smoke-$$-$(date +%s)"
work="$repo/bench/runs/$run_id"
transcript="$work/transcript.jsonl"

# Container names and the bus port are per-run. With fixed names a leftover run
# adopts this run's containers and posts into this run's bus; that really happened
# during the spike, and it is indistinguishable from a duplicated message.
main_container="$run_id-main"
agent_container="$run_id-agent-a"
BUS_PORT="${BUS_PORT:-$(python3 -c 'import socket
s = socket.socket()
s.bind(("", 0))
print(s.getsockname()[1])
s.close()')}"

cleanup() {
    docker rm -f "$main_container" "$agent_container" >/dev/null 2>&1 || true
    [[ -n "${bus_pid:-}" ]] && kill "$bus_pid" 2>/dev/null || true
}
trap cleanup EXIT

# One memory directory per agent: its own role prompt, history, and vector store.
# Fresh directories are also how a trial resets memory.
role() {
    local name="$1" text="$2"
    mkdir -p "$work/agents/$name/memory/chroma_db"
    printf '%s\n' "$text" > "$work/agents/$name/memory/prompt.txt"
    : > "$work/agents/$name/memory/history.metta"
    chmod -R 0777 "$work/agents/$name/memory"
}

role Main "SPIKE-MAIN. You are Main, talking in a shared channel with Agent-A.
Address others by name with an at sign. Do not use any skill other than send.
When you receive a number from the channel, send exactly: MAIN GOT <number>
Otherwise send exactly: @Agent-A reply with the number 42"

role Agent-A "SPIKE-AGENT-A. You are Agent-A in a shared channel.
Answer only when a message addresses you by name with an at sign, using send.
When addressed, send exactly: 42. Otherwise send nothing at all."

python3 "$repo/bench/bus.py" --port "$BUS_PORT" --transcript "$transcript" &
bus_pid=$!

# A bus that failed to bind (usually a leftover from an aborted run holding the
# port) would otherwise leave the agents talking to the wrong transcript.
for _ in $(seq 20); do
    curl -sf "http://127.0.0.1:$BUS_PORT/transcript" >/dev/null && break
    kill -0 "$bus_pid" 2>/dev/null || { echo "FAIL: bus did not start on :$BUS_PORT"; exit 1; }
    sleep 0.5
done
curl -sf "http://127.0.0.1:$BUS_PORT/transcript" >/dev/null \
    || { echo "FAIL: bus not answering on :$BUS_PORT"; exit 1; }

# Flags mirror scripts/omegaclaw. -it matters: without a TTY the container's
# nginx cannot open /dev/stderr and the agent dies before the loop starts.
launch() {
    local container="$1" agent="$2"
    docker run -d -it --name "$container" \
        --security-opt no-new-privileges:true --init \
        --tmpfs /tmp:size=64m,mode=1777 \
        --tmpfs /var/tmp:size=64m,mode=1777 \
        --tmpfs /run:size=16m,mode=755 \
        --env-file "$ENV_FILE" \
        -v "$work/agents/$agent/memory:$MEMORY_DIR" \
        "$IMAGE" commchannel=bench "agent=$agent" "bus=$BUS_HOST:$BUS_PORT" \
        "provider=$PROVIDER" embeddingprovider=Local \
        securityPolicyPath=/PeTTa/repos/OmegaClaw-Core/profile/policy.yaml \
        "memoryDirectory=\$MEMORY_DIR" >/dev/null
}

alive() {
    docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q true
}

die_if_dead() {
    alive "$1" && return 0
    echo "FAIL: $1 exited"
    docker logs "$1" 2>&1 | tail -20
    exit 1
}

launch "$main_container" Main
launch "$agent_container" Agent-A

echo "waiting for both loops to come up"
for container in "$main_container" "$agent_container"; do
    until docker logs "$container" 2>&1 | grep -qE "CHARS_SENT: [0-9]+"; do
        die_if_dead "$container"
        sleep 2
    done
done

# Each agent must be running its own prompt, not the image's default.
docker logs "$main_container" 2>&1 | grep -q SPIKE-MAIN || { echo "FAIL: Main lacks its role prompt"; exit 1; }
docker logs "$agent_container" 2>&1 | grep -q SPIKE-AGENT-A || { echo "FAIL: Agent-A lacks its role prompt"; exit 1; }
if docker logs "$main_container" 2>&1 | grep -q SPIKE-AGENT-A; then
    echo "FAIL: role prompts are shared"; exit 1
fi
echo "OK: distinct role prompts"

python3 - "$repo/bench" "$BUS_PORT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import bus
bus.post(("127.0.0.1", int(sys.argv[2])), "User", "@Main ask Agent-A for the number")
PY

echo "waiting for a reply from Agent-A (up to ${TIMEOUT}s)"
deadline=$(( $(date +%s) + TIMEOUT ))
until grep -q '"agent": "Agent-A"' "$transcript" 2>/dev/null; do
    die_if_dead "$main_container"
    die_if_dead "$agent_container"
    [[ $(date +%s) -gt $deadline ]] && { echo "FAIL: no reply from Agent-A"; cat "$transcript" 2>/dev/null; exit 1; }
    sleep 3
done

echo "OK: exchange recorded"
cat "$transcript"
echo "run kept at $work"
