"""Communication channel that joins an agent to the benchmark bus.

The benchmark runs several agents against one shared channel, so each agent needs
its own name on that channel. Both the name and the bus address arrive as
`<key>=<value>` command-line arguments, read through the config module, which is
the practical way to get per-agent settings into the container: `entrypoint.sh`
rebuilds the environment from a fixed allowlist, so an invented environment
variable would never reach the agent.

    run.metta commchannel=bench agent=Agent-A bus=172.17.0.1:9770

`receive` blocks on the bus for up to the poll timeout. The agent loop calls the
LLM once per iteration whether or not input arrived, so blocking here is what
keeps an agent that is waiting to be addressed from spending a request per second.
Messages are handed to the agent as `<author>: <text>` — in one shared channel it
has to know who is talking.
"""

import bus
import channels
import config
import helper


class BenchChannel(channels.CommChannel):

    def __init__(self):
        super().__init__()
        self._name = "Main"
        self._address = ("172.17.0.1", 9770)

    def start(self) -> None:
        self._name = config.config_get_by_key("agent", self._name)
        host, _, port = str(config.config_get_by_key("bus", "")).partition(":")
        if host and port:
            self._address = (host, int(port))

    def stop(self) -> None:
        """Nothing to release: the poll is a plain synchronous request."""

    def receive(self) -> str:
        message = bus.poll(self._address, self._name)
        if message is None:
            return ""
        author, text = message
        return f"{author}: {text}"

    def send(self, message: str) -> None:
        # The loop broadcasts the agent's version once at startup (src/loop.metta).
        # That is framework noise, not agent behaviour: in a shared channel it costs
        # every other agent a turn to read and inflates the message count the scorer
        # reads, so it never reaches the transcript.
        if message.strip() == helper.omegaclaw_version():
            return
        bus.post(self._address, self._name, message)


def loadOmegaClawPlugin():
    channels.registerCommChannel("bench", BenchChannel())
