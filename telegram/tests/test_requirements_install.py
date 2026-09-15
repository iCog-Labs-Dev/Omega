"""Who installs the plugin's dependencies, and when the plugin can stop.

The plugin declares what it imports in requirements.txt, and something has to
install it. Core does not: its build installs core's own requirements and
nothing else, so aiogram is missing and the loader fails on import, which takes
start-up with it. run-plugin covers that by patching the copy of core it builds
from — a workaround for a gap in core, not a feature of the plugin.

That gap is being closed in core, so these tests are mostly about the handover.
Two hold the guard honest, so the patch stands down against a core that already
installs plugin requirements instead of adding a second copy. The third is the
canary: it passes while the patch still has something to add and fails once core
carries this on every branch we build from. A failure there is the signal to
delete the patch from run-plugin, the way commit 146f09d dropped the spliced
/telegram-file/ route once core routed it.
"""
import re
import subprocess

import pytest

from conftest import MINIMAL_DOCKERFILE, REPO_ROOT

# Core refs the plugin is built against, and what each one is for. A ref that is
# not fetched here is skipped rather than assumed. The fork's telegram branch is
# deliberately absent: it already installs plugin requirements, which is why the
# bot runs there and nowhere else, so it says nothing about core.
_CORE_REFS = (
    "asi-alliance/main",        # upstream tip
    "icog/dev/new-feature",     # the branch upstream merges from
    "icog/dev/bug-fix",         # its ancestor, still built from
)

# Core's own step, kept in step with the Dockerfile it comes from. If core writes
# it differently, run-plugin's grep stops matching and the "left alone" test
# fails — which is the warning we want, not a false alarm.
_CORE_STEP = """\
COPY ./requirements.txt /tmp/requirements.txt
RUN --mount=type=bind,source=plugins,target=/tmp/plugins \\
    requirements=(-r /tmp/requirements.txt); \\
    for plugin_requirements in /tmp/plugins/*/requirements.txt; do \\
      if [ -f "$plugin_requirements" ]; then \\
        requirements+=(-r "$plugin_requirements"); \\
      fi; \\
    done; \\
    python3 -m pip install --no-cache-dir --break-system-packages "${requirements[@]}" \\
 && python3 -m pip check
"""

# A core that installs the same files a different way. run-plugin must recognise
# this too: what makes the patch redundant is the behaviour, not the spelling.
_ANOTHER_CORE_STEP = """\
COPY ./requirements.txt /tmp/requirements.txt
RUN find /tmp/plugins -name requirements.txt -printf '-r %p\\n' \\
    | xargs python3 -m pip install --no-cache-dir -r /tmp/requirements.txt
"""

_SAYS_PATCHED = "teaching the Dockerfile to install plugin requirements"


def _core_dockerfile(ref):
    """Core's Dockerfile at a ref, or None if the ref is not fetched here."""
    result = subprocess.run(
        ["git", "show", f"{ref}:Dockerfile"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    return result.stdout if result.returncode == 0 else None


@pytest.mark.parametrize("step", [_CORE_STEP, _ANOTHER_CORE_STEP])
def test_a_core_that_installs_plugin_requirements_is_left_alone(step, fake_core, prepare):
    """Patching a core that already does this gives two installs of the same
    files, and the second one resolves on its own — the silent downgrade the
    single invocation exists to prevent."""
    dockerfile = MINIMAL_DOCKERFILE + "\n" + step
    core = fake_core(dockerfile=dockerfile)

    said, context = prepare(core)

    assert (context / "Dockerfile").read_text() == dockerfile
    assert _SAYS_PATCHED not in said


def test_a_core_that_does_not_gets_the_patch(fake_core, prepare):
    """Until core lands this, the patch is the only thing that puts aiogram in
    the image, and without it the plugin cannot load at all."""
    core = fake_core(dockerfile=MINIMAL_DOCKERFILE)

    said, context = prepare(core)

    patched = (context / "Dockerfile").read_text()
    assert _SAYS_PATCHED in said
    assert "plugins/*/requirements.txt" in patched
    # Before the runtime stage: packages installed after it land in the wrong one.
    assert patched.index("requirements.txt") < patched.index("FROM scratch AS runtime")


def test_the_patch_still_has_something_to_add():
    """Fails when core installs plugin requirements on every branch we build from.

    That failure is not a regression, it is the handover: delete the patch from
    run-plugin, delete this test with it, and keep the guard tests above, which
    then describe the only behaviour left.

    A clone without core's remotes cannot answer the question, so it skips rather
    than guessing either way. The signal only works where the refs are fetched.
    """
    cores = {ref: _core_dockerfile(ref) for ref in _CORE_REFS}
    fetched = {ref: text for ref, text in cores.items() if text is not None}
    if not fetched:
        pytest.skip(f"none of {', '.join(_CORE_REFS)} are fetched here")

    without = sorted(
        ref for ref, text in fetched.items()
        if not re.search(r"plugins.*requirements\.txt", text)
    )
    assert without, (
        "every core branch now installs plugin requirements: "
        f"{', '.join(sorted(fetched))}. Drop the Dockerfile patch from run-plugin."
    )
