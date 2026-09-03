import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import channels
from config import config_get_by_key
from helper import omegaclaw_version, projectRootDirectory
from logger import get_logger

logger = get_logger(__name__)

FETCH_TIMEOUT = 10.0
MAX_NOTES_CHARS = 20000
MARKER_FILE = "announced_release"
VERSION_PREFIX = "OmegaClaw version="
NOT_A_RELEASE = re.compile(r"(-\d+-g[0-9a-f]{7,}$)|(-dirty$)|(^[0-9a-f]{7,40}$)")

SUMMARY_INSTRUCTIONS = """\
You are OmegaClaw, an agent that has just started up on a new release. Write
the message you will send your users to tell them what changed.

Structure it like this:
- One opening line saying you have been updated, naming the release.
- One sentence on what the release is about as a whole.
- Then a section for each kind of change, in this order, keeping only the ones
  the notes actually support: What's new, Improvements, Fixes, Security. Put
  the section name on a line of its own, then its bullets under it.
- One closing line on what the release means for the people using you.

Rules:
- Reply with the message text only: no preamble, no surrounding quotes, no
  s-expressions, no markdown headings, no links.
- Bullets start with "- ", one short line each, no full stop at the end, at
  most six to a section.
- Write the Security section as one or two plain sentences rather than bullets.
- Stay under 300 words, and say nothing the notes below do not support."""


@dataclass(frozen=True)
class ReleaseTag:
    name: str

    @classmethod
    def of_build(cls, version=None):
        version = omegaclaw_version() if version is None else str(version)
        if not version.startswith(VERSION_PREFIX):
            return None
        name = version[len(VERSION_PREFIX):].strip()
        if not name or NOT_A_RELEASE.search(name):
            return None
        return cls(name)


def announcing_tag():
    named = str(config_get_by_key("releaseTag", "") or "").strip()
    return ReleaseTag(named) if named else ReleaseTag.of_build()


def marker_path():
    return Path(projectRootDirectory()) / "memory" / MARKER_FILE


def announced_release():
    try:
        return marker_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def fetch_release(tag):
    base = str(config_get_by_key("releaseApiURL", "https://api.github.com")).rstrip("/")
    repo = str(config_get_by_key("releaseRepo", "iCog-Labs-Dev/mettaclaw")).strip("/")
    url = f"{base}/repos/{repo}/releases/tags/{tag.name}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OmegaClaw",
    })
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as answer:
        logger.info(f"No release notes for {tag.name}: {url} answered {answer.code}")
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.warning(f"Could not read release notes for {tag.name} from {url}: {e}")
        return None
    return payload if isinstance(payload, dict) else None


def fit_notes(body):
    notes = str(body or "").strip()
    if len(notes) <= MAX_NOTES_CHARS:
        return notes
    kept = notes[:MAX_NOTES_CHARS]
    # Notes shaped as a title, a blank line and then an unbroken bullet run have
    # their last blank line in the first line or two, so a boundary that early
    # would drop the whole changelog. Take the next one down instead.
    keep_at_least = MAX_NOTES_CHARS * 4 // 5
    for boundary in ("\n\n", "\n"):
        edge = kept.rfind(boundary)
        if edge >= keep_at_least:
            return kept[:edge].rstrip()
    return kept.rstrip()


def summary_request(tag, payload):
    notes = fit_notes((payload or {}).get("body"))
    if not notes:
        return ""
    return "\n".join((SUMMARY_INSTRUCTIONS,
                      "",
                      f"VERSION: {tag.name}",
                      f"TITLE: {payload.get('name') or tag.name}",
                      f"PUBLISHED: {payload.get('published_at') or ''}",
                      f"LINK: {payload.get('html_url') or ''}",
                      "RELEASE NOTES:",
                      notes))


def _chat(request):
    # Imported here rather than at module scope so reading this module - a test,
    # a --version call - does not pull in the provider registry.
    import providers
    return providers.llmProviderChat(request,
                                     int(config_get_by_key("maxOutputToken", 6000)),
                                     str(config_get_by_key("reasoningMode", "medium")))


def announcement(summary):
    text = str(summary).strip()
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def mark_announced(tag):
    path = marker_path()
    try:
        path.write_text(f"{tag.name}\n", encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not record the announced release: {e}")
        return False
    logger.info(f"Announced release {tag.name}")
    return True


def announce():
    try:
        tag = announcing_tag()
        if tag is None:
            logger.info("This build is not a release, nothing to announce")
            return ""
        if tag.name == announced_release():
            logger.info(f"Release {tag.name} was announced already")
            return ""
        request = summary_request(tag, fetch_release(tag))
        if not request:
            return ""
        message = announcement(_chat(request))
        if not message:
            logger.warning("The release summary came back empty, announcing nothing")
            return ""
        channels.commChannelSend(message)
        mark_announced(tag)
        return message
    except Exception as e:
        logger.exception(f"Giving up on the release announcement: {e}")
        return ""
