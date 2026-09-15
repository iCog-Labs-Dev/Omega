"""Scaffolding for the tests that drive run-plugin against a core.

run-plugin changes a copy of core rather than core itself, so what there is to
check is the copy: what it patched, and what it left alone. Building a real core
takes ten minutes and a network, so these hand it the smallest tree it accepts
and stop at --prepare-only, before docker is involved.
"""
import subprocess
from pathlib import Path
from typing import Optional

import pytest

_HERE = Path(__file__).resolve().parent
PLUGIN_DIR = _HERE.parent
REPO_ROOT = PLUGIN_DIR.parent
RUN_PLUGIN = REPO_ROOT / "run-plugin"

# Enough of a Dockerfile to be patched: a builder stage, and a second stage for
# the patch to insert itself before.
MINIMAL_DOCKERFILE = """\
FROM scratch AS builder
RUN :

FROM scratch AS runtime
RUN :
"""


@pytest.fixture
def fake_core(tmp_path):
    """Build the smallest tree run-plugin --prepare-only accepts as a core."""
    def make(dockerfile: str = MINIMAL_DOCKERFILE,
             nginx_template: Optional[str] = None) -> Path:
        root = tmp_path / "core"
        (root / "config").mkdir(parents=True)
        (root / "config" / "plugins.yaml").write_text(
            '- name: telegram\n  loader: python\n  location: "{REPO}/channels"\n'
        )
        (root / "Dockerfile").write_text(dockerfile)
        if nginx_template is not None:
            (root / "proxy").mkdir(parents=True)
            (root / "proxy" / "nginx.conf.template").write_text(nginx_template)
        return root

    return make


@pytest.fixture
def prepare(tmp_path):
    """Assemble the build context; hand back what run-plugin said and wrote."""
    def run(core: Path) -> tuple:
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
        assert result.returncode == 0, result.stdout + result.stderr
        context = cache / "omega-plugin-run" / "context"
        return result.stdout + result.stderr, context

    return run
