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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Provider modules import core both as `src.x` and as bare `x`, so both the
# repository root and src/ have to be importable.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import release  # noqa: E402

HEADING = re.compile(r"^##\s+\[?([^\]\s]+)\]?\s*$")
REHEARSAL_ROUND = re.compile(r"\.\d+$")


def released_version(tag):
    """The section a tag ships. A pre-prod rehearsal reads the prod section."""
    if not tag.startswith("pre-"):
        return tag
    return REHEARSAL_ROUND.sub("", tag[len("pre-"):])


def changelog_section(tag):
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    version = released_version(tag)
    for start, line in enumerate(lines):
        found = HEADING.match(line)
        if not found or found.group(1) != version:
            continue
        end = start + 1
        while end < len(lines) and not HEADING.match(lines[end]):
            end += 1
        return "\n".join(lines[start + 1:end]).strip()
    raise SystemExit(f"CHANGELOG.md has no '## {version}' section for tag {tag}")


def chat(request, provider):
    """Answer the request the way a running agent would.

    Providers are plugins: a module under providers/ whose loadOmegaPlugin()
    registers it under the name the command line selects. Start-up does that
    through the plugin loader; here the one module that is needed is imported
    directly, so a preview pulls in nothing else."""
    import importlib
    import providers

    sys.path.insert(0, str(ROOT / "providers"))
    try:
        importlib.import_module(provider.lower()).loadOmegaPlugin()
    except ModuleNotFoundError as e:
        raise SystemExit(f"no provider module for {provider}: {e}")
    if provider not in providers._llmProviderRegistry:
        raise SystemExit(
            f"{provider} did not register. Registered: "
            f"{sorted(providers._llmProviderRegistry) or '(none)'}")

    providers.llmProviderStart(provider)
    try:
        return release._chat(request)
    except RuntimeError as e:
        # Core says exactly which credential is missing; a traceback would bury it.
        raise SystemExit(str(e))


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
