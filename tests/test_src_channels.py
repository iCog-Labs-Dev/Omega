import channels

class TestCommChannel(channels.CommChannel):

    started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        raise NotImplementedError()

    def receive(self) -> str:
        raise NotImplementedError()

    def send(self, message: str) -> None:
        raise NotImplementedError()


def test_commchannel_config():
    channel = TestCommChannel()
    channels.registerCommChannel("Test", channel)
    channels.commChannelStart("Test")
    assert channel.started


def test_commchannel_tools_are_enabled_by_default():
    channel = TestCommChannel()
    channels.registerCommChannel("TestTools", channel)
    channels.commChannelStart("TestTools")
    assert channels.commChannelIsToolDisabled("websearch") is False


def test_commchannel_exposes_runtime_tool_restrictions():
    channel = TestCommChannel()
    channel.is_tool_disabled = lambda tool_name: tool_name == "websearch"
    channels.registerCommChannel("RestrictedTools", channel)
    channels.commChannelStart("RestrictedTools")
    assert channels.commChannelIsToolDisabled("websearch") is True
    assert channels.commChannelIsToolDisabled("send") is False

