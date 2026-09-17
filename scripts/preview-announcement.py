#!/usr/bin/env python3
"""Show the message a release would announce, without deploying anything.

The announcement is only visible once a container starts, which puts a build and
a deploy between writing the notes and reading what they became. This runs the
same path locally: the notes are turned into the same request `release.py`
builds, the model answers it, and the reply goes through the same formatting the
channel would apply.

    scripts/preview-announcement.py --tag prod-2026-09-17 --prompt-only
    OPENROUTER_API_KEY=... scripts/preview-announcement.py --tag prod-2026-09-17

Notes come from CHANGELOG.md by default, so wording can be checked before it is
published anywhere. `--published` reads the GitHub release instead, which is
what the bot will actually see. `--prompt-only` stops before the model, needs no
credentials and reaches no network.
"""
import argparse
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Provider modules import core both as `src.x` and as bare `x`, so both the
# repository root and src/ have to be importable.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import release  # noqa: E402

_section = SourceFileLoader(
    "changelog_section", str(Path(__file__).resolve().parent / "changelog-section.py")
).load_module()


def changelog_section(tag):
    """The CHANGELOG section a tag ships, read the way the publisher reads it."""
    version = _section.released_version(tag)
    body = _section.section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if not body:
        raise SystemExit(f"CHANGELOG.md has no '## {version}' section for tag {tag}")
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--published", action="store_true",
                        help="read the GitHub release rather than CHANGELOG.md")
    parser.add_argument("--repo", default="iCog-Labs-Dev/Omega")
    parser.add_argument("--prompt-only", action="store_true",
                        help="print the request and stop, without calling the model")
    parser.add_argument("--provider", default="OpenRouter",
                        help="LLM provider to answer with, as the bot is run with")
    parser.add_argument("--model", default="",
                        help="model to answer with; the provider's default otherwise")
    args = parser.parse_args()

    config._CONFIG = {"releaseRepo": args.repo, "releaseTag": args.tag}
    if args.model:
        config._CONFIG["model"] = args.model
    tag = release.ReleaseTag(args.tag)

    if args.published:
        payload = release.fetch_release(tag)
        if payload is None:
            raise SystemExit(f"no published release for {args.tag} in {args.repo}")
    else:
        payload = {"tag_name": args.tag, "name": args.tag,
                   "body": changelog_section(args.tag),
                   "published_at": "", "html_url": ""}

    request = release.summary_request(tag, payload)
    if not request:
        raise SystemExit("the notes are empty, so nothing would be announced")

    notes = release.fit_notes(payload.get("body"))
    print(f"# notes {len(payload['body'])} chars, "
          f"{'truncated to ' + str(len(notes)) if len(notes) < len(payload['body'].strip()) else 'not truncated'}"
          f" (cap {release.MAX_NOTES_CHARS})", file=sys.stderr)

    if args.prompt_only:
        print(request)
        return

    message = release.announcement(chat(request, args.provider))
    if not message:
        raise SystemExit("the model returned nothing, so nothing would be announced")

    # announcement() escapes newlines for the channel; undo that for reading.
    print(message.replace("\\n", "\n"))
    words = len(message.split())
    print(f"\n# {len(message)} chars on the wire, about {words} words",
          file=sys.stderr)


if __name__ == "__main__":
    main()
