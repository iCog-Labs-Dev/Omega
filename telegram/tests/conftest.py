"""Scaffolding for the tests that drive run-plugin against a core.

run-plugin adds the plugin to a copy of core without changing any core file, so
what there is to check is the copy: what it added, and that the rest is core as
it was. Building a real core takes ten minutes and a network, so these hand it
the smallest tree it accepts and stop at --prepare-only, before docker is
involved.
"""
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

_HERE = Path(__file__).resolve().parent
PLUGIN_DIR = _HERE.parent
REPO_ROOT = PLUGIN_DIR.parent
RUN_PLUGIN = REPO_ROOT / "run-plugin"

# Enough of a Dockerfile to stand for a core that installs plugin requirements.
MINIMAL_DOCKERFILE = """\
FROM scratch AS builder
RUN for req in /tmp/plugins/*/requirements.txt; do :; done

FROM scratch AS runtime
RUN :
"""

# Stand-ins for the core source run-plugin reads to decide what a core supports.
# Each names the feature the way core's code does.
PLUGIN_PY_WITH_DROP_INS = 'manifests += sorted(config_dir.joinpath("plugins.d").glob("*.yaml"))\n'
CHANNELS_PY_WITH_FEATURES = (
    "def prompt_file(self) -> str:\n"
    "def commChannelIsToolDisabled(tool_name):\n"
)


@pytest.fixture(autouse=True)
def _fresh_reply_gate():
    """Start each test with a reply gate that has seen no turn yet.

    The channel is one module-level object, so turns counted by one test would
    otherwise decide whether the next test's sends are delivered."""
    channel_module = sys.modules.get("telegram_channel")
    if channel_module is not None:
        from reply_gate import ReplyGate
        channel_module._channel.reply_gate = ReplyGate()
        channel_module._channel._notice_alerted_at = {}
    yield


@pytest.fixture
def fake_core(tmp_path):
    """Build the smallest tree run-plugin --prepare-only accepts as a core."""
    def make(dockerfile: str = MINIMAL_DOCKERFILE,
             nginx_template: Optional[str] = None,
             plugin_py: str = PLUGIN_PY_WITH_DROP_INS,
             channels_py: str = CHANNELS_PY_WITH_FEATURES) -> Path:
        root = tmp_path / "core"
        (root / "config").mkdir(parents=True)
        (root / "config" / "plugins.yaml").write_text(
            '- name: telegram\n  loader: python\n  location: "{REPO}/channels"\n'
        )
        (root / "channels").mkdir()
        (root / "channels" / "telegram.py").write_text("# core's own channel\n")
        (root / "memory").mkdir()
        (root / "memory" / "tg_prompt.txt").write_text("TELEGRAM ROUTING:\n")
        (root / "src").mkdir()
        (root / "src" / "plugin.py").write_text(plugin_py)
        (root / "src" / "channels.py").write_text(channels_py)
        (root / "Dockerfile").write_text(dockerfile)
        if nginx_template is not None:
            (root / "proxy").mkdir(parents=True)
            (root / "proxy" / "nginx.conf.template").write_text(nginx_template)
        return root

    return make


@pytest.fixture
def prepare(tmp_path):
    """Assemble the build context; hand back what run-plugin said and wrote.

    With expect_ok=False the run may fail, and the exit code comes back instead
    of the context."""
    def run(core: Path, expect_ok: bool = True) -> tuple:
        env_file = tmp_path / "env"
        env_file.write_text("TG_BOT_TOKEN=placeholder\n")
        cache = tmp_path / "cache"

        result = subprocess.run(
            [str(RUN_PLUGIN), "--prepare-only",
             "--core-dir", str(core),
             "--plugin-dir", str(PLUGIN_DIR),
             "--env-file", str(env_file)],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
                 "XDG_CACHE_HOME": str(cache)},
        )
        said = result.stdout + result.stderr
        if not expect_ok:
            return said, result.returncode
        assert result.returncode == 0, said
        return said, cache / "omega-plugin-run" / "context"

    return run
