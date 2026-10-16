import channels

from test_src_channels import TestCommChannel


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
