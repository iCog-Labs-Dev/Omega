"""run-plugin adds the plugin to core without changing a core file.

It used to patch the core it built from: rewrite the manifest, delete core's own
Telegram channel and prompt, and teach the Dockerfile to install plugin
requirements. Core now has a supported way to do each, so run-plugin uses those
and refuses a core that lacks the two the plugin cannot load without.
"""
import filecmp
from pathlib import Path

import pytest

from conftest import MINIMAL_DOCKERFILE

_DROP_IN = '- name: telegram\n  loader: metta\n  location: "{REPO}/plugins/telegram"\n'


def _core_files(root: Path) -> set:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


def test_core_files_are_left_as_they_were(fake_core, prepare):
    core = fake_core()

    _, context = prepare(core)

    for name in _core_files(core):
        assert filecmp.cmp(core / name, context / name, shallow=False), name


def test_the_plugin_is_added_with_a_drop_in(fake_core, prepare):
    said, context = prepare(fake_core())

    assert (context / "config" / "plugins.d" / "telegram.yaml").read_text() == _DROP_IN
    assert (context / "plugins" / "telegram" / "telegram.metta").is_file()
    assert "added config/plugins.d/telegram.yaml" in said


def test_core_channel_and_prompt_stay_in_the_image(fake_core, prepare):
    """The drop-in replaces core's 'telegram' entry, so core's channel is never
    loaded and core's prompt is only added by that channel. Deleting the files
    is no longer needed."""
    _, context = prepare(fake_core())

    assert (context / "channels" / "telegram.py").is_file()
    assert (context / "memory" / "tg_prompt.txt").is_file()


def test_a_core_without_drop_ins_is_refused(fake_core, prepare):
    said, code = prepare(fake_core(plugin_py="def listPlugins(): pass\n"), expect_ok=False)

    assert code != 0
    assert "does not read config/plugins.d" in said


def test_a_core_that_does_not_install_plugin_requirements_is_refused(fake_core, prepare):
    dockerfile = MINIMAL_DOCKERFILE.replace(
        "RUN for req in /tmp/plugins/*/requirements.txt; do :; done", "RUN :")

    said, code = prepare(fake_core(dockerfile=dockerfile), expect_ok=False)

    assert code != 0
    assert "does not install plugins/*/requirements.txt" in said


@pytest.mark.parametrize("missing, warning", [
    ("def prompt_file", "core's routing prompt next to its own"),
    ("def commChannelIsToolDisabled", "/togglesearch"),
])
def test_a_core_missing_a_feature_is_named(missing, warning, fake_core, prepare):
    channels_py = "".join(
        line + "\n" for line in [
            "def prompt_file(self) -> str:",
            "def commChannelIsToolDisabled(tool_name):",
        ] if not line.startswith(missing))

    said, _ = prepare(fake_core(channels_py=channels_py))

    assert warning in said


def test_a_complete_core_gets_no_warnings(fake_core, prepare):
    said, _ = prepare(fake_core())

    assert "!" not in said
