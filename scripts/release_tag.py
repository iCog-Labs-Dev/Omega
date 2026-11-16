#!/usr/bin/env python3
"""Release tags for the Telegram bot: telegram-vMAJOR.MINOR.PATCH.

Omega_CustomBots holds several bots, so the bot's name leads the tag. A release
is rehearsed on staging first as telegram-vMAJOR.MINOR.PATCH-rc.N. A rehearsal
announces the notes of the release it rehearses, and each new attempt takes the
next N, because the bot does not announce a tag it has announced before.

    tag = ReleaseTag.parse("telegram-v1.3.0-rc.2")
    tag.is_rehearsal   # True
    tag.release        # telegram-v1.3.0

A deploy checks its tag before building. It refuses a tag that is malformed,
not newer than the last release, or has no notes in CHANGELOG.md:

    scripts/release_tag.py check telegram-v1.3.0-rc.1
"""
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path

PREFIX = "telegram-v"
_TAG = re.compile(r"^telegram-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.([1-9]\d*))?$")
_HEADING = re.compile(r"^##\s+\[?([^\]\s]+)\]?\s*$")
_REPO_ROOT = Path(__file__).resolve().parent.parent


class InvalidTag(ValueError):
    """A tag that is not telegram-vMAJOR.MINOR.PATCH or a -rc.N rehearsal of one."""


@total_ordering
@dataclass(frozen=True)
class ReleaseTag:
    """One release of the bot, or one rehearsal of a release.

    Tags order by version, and a release comes after all of its rehearsals, so
    telegram-v1.3.0-rc.2 < telegram-v1.3.0 < telegram-v1.3.1. Build one with
    parse(); a ReleaseTag always names a valid tag.
    """
    major: int
    minor: int
    patch: int
    rehearsal_round: int | None = None

    @classmethod
    def parse(cls, name):
        """The tag `name` names, or InvalidTag."""
        found = _TAG.match(name)
        if not found:
            raise InvalidTag(f"'{name}' is not a release tag: use telegram-vMAJOR.MINOR.PATCH, "
                             f"or telegram-vMAJOR.MINOR.PATCH-rc.N to rehearse one on staging")
        major, minor, patch, rehearsal = found.groups()
        return cls(int(major), int(minor), int(patch), int(rehearsal) if rehearsal else None)

    @property
    def is_rehearsal(self):
        return self.rehearsal_round is not None

    @property
    def release(self):
        """The release this tag ships, or rehearses."""
        return ReleaseTag(self.major, self.minor, self.patch)

    def __str__(self):
        name = f"{PREFIX}{self.major}.{self.minor}.{self.patch}"
        return f"{name}-rc.{self.rehearsal_round}" if self.is_rehearsal else name

    def __lt__(self, other):
        return self._order() < other._order()

    def _order(self):
        """Version first, then a release after its rehearsals."""
        return (self.major, self.minor, self.patch, not self.is_rehearsal, self.rehearsal_round or 0)


def section(changelog, heading):
    """The body of CHANGELOG.md's `## heading` section, or "" when there is none."""
    lines = changelog.splitlines()
    for start, line in enumerate(lines):
        found = _HEADING.match(line)
        if not found or found.group(1) != heading:
            continue
        end = start + 1
        while end < len(lines) and not _HEADING.match(lines[end]):
            end += 1
        return "\n".join(lines[start + 1:end]).strip()
    return ""


def check(name, existing, changelog):
    """What stops `name` from being deployed, given the tags that already exist
    and the text of CHANGELOG.md. An empty list means it may go."""
    try:
        tag = ReleaseTag.parse(name)
    except InvalidTag as e:
        return [str(e)]

    problems = []
    releases = [t for t in map(_release_or_none, existing) if t is not None]
    if releases and max(releases) >= tag.release:
        problems.append(f"{tag.release} is not newer than the last release, {max(releases)}")
    if not section(changelog, str(tag.release)):
        problems.append(f"CHANGELOG.md has no '## {tag.release}' section for {tag} to announce")
    return problems


def _release_or_none(name):
    """The release `name` tags, or None for a rehearsal or any other tag."""
    try:
        tag = ReleaseTag.parse(name)
    except InvalidTag:
        return None
    return None if tag.is_rehearsal else tag


def changelog_path():
    """CHANGELOG.md at the repository root, unless CHANGELOG_PATH names another."""
    return Path(os.environ.get("CHANGELOG_PATH") or _REPO_ROOT / "CHANGELOG.md")


def _existing_tags(leaving_out):
    """The repository's release tags, without the one being deployed: a deploy
    runs after its own tag was pushed."""
    listed = subprocess.run(["git", "-C", str(_REPO_ROOT), "tag", "--list", f"{PREFIX}*"],
                            capture_output=True, text=True, check=True).stdout.split()
    return [name for name in listed if name != leaving_out]


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "check":
        sys.exit("usage: release_tag.py check <tag>")
    name = sys.argv[2]
    problems = check(name, _existing_tags(name), changelog_path().read_text(encoding="utf-8"))
    if problems:
        sys.exit("\n".join(problems))
    tag = ReleaseTag.parse(name)
    print(f"{tag}: rehearses {tag.release}" if tag.is_rehearsal else f"{tag}: a release")


if __name__ == "__main__":
    main()
