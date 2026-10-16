"""Test channel for the websearch veto test in src_skills.metta."""

import pathlib
import sys

_SRC = str(pathlib.Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.append(_SRC)

import channels


class _Channel(channels.CommChannel):

    def __init__(self, disabled):
        self._disabled = set(disabled)

    def is_tool_disabled(self, tool_name: str) -> bool:
        return tool_name in self._disabled


def start(name, disabled_tool):
    channels.registerCommChannel(name, _Channel([disabled_tool]))
    channels.commChannelStart(name)
    return True
