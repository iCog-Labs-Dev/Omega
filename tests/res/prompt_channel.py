"""Test channels for the channel prompt extension test in src_skills.metta."""

import pathlib
import sys

_SRC = str(pathlib.Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.append(_SRC)

import channels


class _Channel(channels.CommChannel):

    def __init__(self, prompt_file):
        self._prompt_file = prompt_file

    def prompt_file(self) -> str:
        return self._prompt_file


def start(name, prompt_file):
    channels.registerCommChannel(name, _Channel(prompt_file))
    channels.commChannelStart(name)
    return True
