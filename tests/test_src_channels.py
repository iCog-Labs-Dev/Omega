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



def test_channel_has_no_prompt_file_by_default():
    channel = TestCommChannel()
    channels.registerCommChannel("NoPrompt", channel)
    channels.commChannelStart("NoPrompt")
    assert channels.commChannelPromptFile() == ""


class PromptCommChannel(TestCommChannel):

    def prompt_file(self) -> str:
        return "tg_prompt.txt"


def test_prompt_file_comes_from_the_started_channel():
    channels.registerCommChannel("telegram", PromptCommChannel())
    channels.commChannelStart("telegram")
    assert channels.commChannelPromptFile() == "tg_prompt.txt"

    channels.registerCommChannel("telegram", TestCommChannel())
    channels.commChannelStart("telegram")
    assert channels.commChannelPromptFile() == ""
