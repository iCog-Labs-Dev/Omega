#!/usr/bin/env python3
"""Print one release's section of CHANGELOG.md.

A release tag names the section it ships. `telegram-v1.3.0-rc.2` is a rehearsal
of `telegram-v1.3.0`, so it prints that release's section: staging then
announces the very text production will announce, rather than a copy that can
drift from it. See release_tag.py for the tags.

    scripts/changelog-section.py telegram-v1.3.0-rc.2   # the telegram-v1.3.0 section
    scripts/changelog-section.py telegram-v1.3.0        # the same section

Tags from before version numbers still find their sections: `pre-prod-2026-09-17`
and `pre-prod-2026-09-17.2` print `prod-2026-09-17`.

Exits non-zero when the section is missing, so a release cannot be published
with an empty body.
"""
import re
import sys

from release_tag import PREFIX, ReleaseTag, InvalidTag, changelog_path, section

_REHEARSAL_ROUND = re.compile(r"\.\d+$")


def released_version(tag):
    """The heading of the section `tag` ships."""
    if tag.startswith(PREFIX):
        return str(ReleaseTag.parse(tag).release)
    if not tag.startswith("pre-"):
        return tag
    return _REHEARSAL_ROUND.sub("", tag[len("pre-"):])


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: changelog-section.py <tag>")
    tag = sys.argv[1]
    try:
        version = released_version(tag)
    except InvalidTag as e:
        sys.exit(str(e))
    path = changelog_path()
    try:
        changelog = path.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot read {path}: {e}")
    body = section(changelog, version)
    if not body:
        sys.exit(f"CHANGELOG.md has no '## {version}' section for tag {tag}")
    print(body)


if __name__ == "__main__":
    main()
