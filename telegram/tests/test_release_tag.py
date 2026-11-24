"""Releases are numbered telegram-vMAJOR.MINOR.PATCH, and rehearsed on staging
as telegram-vMAJOR.MINOR.PATCH-rc.N.

Omega_CustomBots holds several bots, so the prefix says which one a tag
belongs to. A deploy checks its tag before building: a malformed tag, a
version that is not newer than the last release, or one with no notes in
CHANGELOG.md stops it.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_REPO_ROOT, "scripts")

# Inside a built image the plugin has no scripts folder beside it.
if not os.path.exists(os.path.join(_SCRIPTS, "changelog-section.py")):
    pytest.skip("needs the repository's scripts folder", allow_module_level=True)

sys.path.insert(0, _SCRIPTS)
from release_tag import InvalidTag, ReleaseTag, check  # noqa: E402

CHANGELOG = """\
# Changelog

## telegram-v1.1.0

### Added

* Something new.

## telegram-v1.0.0

* The first numbered release.

## prod-2026-09-17

* Notes from the date-tag days.
"""


def test_a_release_names_its_version():
    tag = ReleaseTag.parse("telegram-v1.2.3")
    assert str(tag) == "telegram-v1.2.3"
    assert not tag.is_rehearsal
    assert tag.release == tag


def test_a_rehearsal_names_the_release_it_rehearses():
    tag = ReleaseTag.parse("telegram-v1.2.3-rc.2")
    assert tag.is_rehearsal
    assert tag.release == ReleaseTag.parse("telegram-v1.2.3")


@pytest.mark.parametrize("name", [
    "v1.2.3",                    # no bot prefix
    "telegram-1.2.3",            # no v
    "telegram-v1.2",             # not three numbers
    "telegram-v01.2.3",          # leading zero
    "telegram-v1.2.3-beta.1",    # only rc rehearsals
    "telegram-v1.2.3-rc",        # rehearsal without a number
    "telegram-v1.2.3-rc.0",      # rehearsals count from 1
    "prod-2026-11-16",           # the old date tags
])
def test_anything_else_is_not_a_release_tag(name):
    with pytest.raises(InvalidTag):
        ReleaseTag.parse(name)


def test_versions_compare_as_numbers_and_rehearsals_come_first():
    order = ["telegram-v1.9.0-rc.1", "telegram-v1.9.0-rc.2", "telegram-v1.9.0",
             "telegram-v1.9.1", "telegram-v1.10.0", "telegram-v2.0.0"]
    tags = [ReleaseTag.parse(name) for name in order]
    assert sorted(reversed(tags)) == tags


def test_a_newer_release_with_notes_passes():
    assert check("telegram-v1.1.0", ["telegram-v1.0.0"], CHANGELOG) == []


def test_a_rehearsal_of_the_next_release_passes():
    assert check("telegram-v1.1.0-rc.1", ["telegram-v1.0.0"], CHANGELOG) == []


@pytest.mark.parametrize("tag, existing", [
    ("telegram-v1.0.0", ["telegram-v1.1.0"]),           # older than the last release
    ("telegram-v1.1.0", ["telegram-v1.1.0"]),           # already released
    ("telegram-v1.1.0-rc.3", ["telegram-v1.1.0"]),      # rehearsing a released version
])
def test_a_version_that_is_not_newer_is_refused(tag, existing):
    problems = check(tag, existing, CHANGELOG)
    assert problems and "newer" in problems[0]


def test_a_release_without_notes_is_refused():
    problems = check("telegram-v1.2.0-rc.1", ["telegram-v1.1.0"], CHANGELOG)
    assert problems and "telegram-v1.2.0" in problems[0]


def test_a_malformed_tag_is_refused():
    problems = check("telegram-v1.2", [], CHANGELOG)
    assert problems and "telegram-vMAJOR.MINOR.PATCH" in problems[0]


def test_rehearsals_and_other_tags_do_not_count_as_releases():
    # Earlier rc tags, the date tags, other bots' tags and core builds are not
    # releases of the bot.
    existing = ["telegram-v1.1.0-rc.1", "telegram-v1.1.0-rc.2", "prod-2026-11-16",
                "bgi-slack-v4.0.0", "telegram-core-20261110-405f692",
                "telegram-core-v9.0.0", "telegram-v1.0.0"]
    assert check("telegram-v1.1.0", existing, CHANGELOG) == []


def _section(tag, changelog_path):
    """Run changelog-section.py the way the release steps do."""
    script = os.path.join(_SCRIPTS, "changelog-section.py")
    return subprocess.run([sys.executable, script, tag], capture_output=True, text=True,
                          env={**os.environ, "CHANGELOG_PATH": str(changelog_path)})


@pytest.fixture
def changelog(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)
    return path


def test_a_rehearsal_announces_the_notes_of_its_release(changelog):
    result = _section("telegram-v1.1.0-rc.2", changelog)
    assert result.returncode == 0 and "Something new." in result.stdout


def test_old_date_tags_still_find_their_notes(changelog):
    result = _section("pre-prod-2026-09-17.2", changelog)
    assert result.returncode == 0 and "date-tag days" in result.stdout


def test_the_changelog_has_notes_for_the_first_numbered_release():
    with open(os.path.join(_REPO_ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
        assert check("telegram-v1.0.0", [], handle.read()) == []
