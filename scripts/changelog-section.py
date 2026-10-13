#!/usr/bin/env python3
"""Print one release's section of CHANGELOG.md.

A release tag names the section it ships. `pre-prod-2026-09-17` is a rehearsal
of `prod-2026-09-17`, so the `pre-` is dropped before looking the section up:
staging then announces the very text production will announce, rather than a
copy that can drift from it.

A rehearsal can be run more than once, and a tag cannot be reused once the bot
has recorded it as announced, so `.2`, `.3` and so on may be appended to take a
fresh tag against the same notes.

    scripts/changelog-section.py pre-prod-2026-09-17     # prints the prod section
    scripts/changelog-section.py pre-prod-2026-09-17.2   # the same section

Exits non-zero when the section is missing, so a release cannot be published
with an empty body.
"""
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^##\s+\[?([^\]\s]+)\]?\s*$")
REHEARSAL_ROUND = re.compile(r"\.\d+$")


def section(changelog, version):
    lines = changelog.splitlines()
    for start, line in enumerate(lines):
        found = HEADING.match(line)
        if not found or found.group(1) != version:
            continue
        end = start + 1
        while end < len(lines) and not HEADING.match(lines[end]):
            end += 1
        return "\n".join(lines[start + 1:end]).strip()
    return ""


def released_version(tag):
    if not tag.startswith("pre-"):
        return tag
    return REHEARSAL_ROUND.sub("", tag[len("pre-"):])


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: changelog-section.py <tag>")
    tag = sys.argv[1]
    version = released_version(tag)
    path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
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
