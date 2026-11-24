"""Tell the bot's users what changed when a new release starts up.

On the first loop iteration after a deploy, the plugin fetches the GitHub
release notes for the tag the deploy named, has the model turn them into a
short message for chat users, and sends it once. A marker file in the memory
directory records the announced tag, so a restart does not announce again.

The deploy passes the tag and the repository holding the notes as run
arguments, which core's config reads like any other setting:

    run.metta commchannel=telegram releaseTag=telegram-v2.0.0 releaseRepo=iCog-Labs-Dev/Omega

Without releaseTag nothing is announced. Everything that can fail - the
fetch, the model, the send - logs and gives up, so a release never stops the
bot from starting.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 10.0
MAX_NOTES_CHARS = 20000
MARKER_FILE = "announced_release"
DEFAULT_RELEASE_REPO = "iCog-Labs-Dev/Omega"
TOOL_NAME = "announce_release"
TOOL_ARGUMENT = "message"

SUMMARY_INSTRUCTIONS = """\
You are Omega, an agent that has just started up on a new release. Write
the message you will send your users to tell them what changed.

Write for someone who talks to you in a chat and has no privileged access. The
notes below are written for the people who run you as well, and most of that is
not the reader's to see.

Leave out entirely, even when the notes describe them:
- Administration of any kind: commands only an operator can run, what they are
  called, who may use them, and how they are authorised. Never name a command
  beginning with "/".
- Moderation and alerting machinery, and who receives its alerts.
- Anything showing you can be steered from outside the conversation: being
  paused, muted or silenced, or having your memory cleared. Naming no command
  still tells the room that the control is there.
- Configuration, deployment, environment variables, file paths, and the
  machinery you run on.

Structure it like this:
- One opening line saying you have been updated, naming the release.
- A blank line, then one sentence on what the release is about as a whole. Keep
  it on its own line; do not join it to the opening line.
- Then a section for each kind of change, in this order, keeping only the ones
  the notes still support once the above is removed: What's new, Improvements,
  Fixes, Safety. Put the section name on a line of its own in bold, written as
  **What's new**, with a blank line above it, then its bullets under it.
- A blank line before the closing line too.
- One closing line on what the release means for the people using you.

Rules:
- Reply with the message text only: no preamble, no surrounding quotes, no
  s-expressions, no markdown headings, no links.
- Bold is only for the section names; leave the rest of the text plain.
- Put one emoji immediately before each section name, then a space, then the
  bold name: "\u2728 **What's new**". Never after the name. One per section, a
  different one for each, each recognisably about that section. Emoji appear
  nowhere else: not in the bullets, not in the opening or closing lines.
- Bullets start with "- ", one short line each, no full stop at the end, at
  most six to a section.
- Say what changed for the person reading, not how it was built.
- Write the Safety section as one or two plain sentences rather than bullets,
  and only about what affects an ordinary user, such as what is done with what
  they send. Drop the section if nothing is left once administration is removed.
- Drop any section whose every item you were told to leave out. Do not pad one.
- Stay under 300 words, and say nothing the notes below do not support."""


@dataclass(frozen=True)
class ReleaseTag:
    """A release tag the deploy named, never empty."""
    name: str

    @classmethod
    def configured(cls):
        """The tag in the releaseTag setting, or None when there is none."""
        from config import config_get_by_key
        name = str(config_get_by_key("releaseTag", "") or "").strip()
        return cls(name) if name else None


def marker_path():
    from helper import projectRootDirectory
    return Path(projectRootDirectory()) / "memory" / MARKER_FILE


def announced_release():
    try:
        return marker_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def fetch_release(tag):
    """The GitHub release for tag as a dict, or None if there is none."""
    from config import config_get_by_key
    base = str(config_get_by_key("releaseApiURL", "https://api.github.com")).rstrip("/")
    repo = str(config_get_by_key("releaseRepo", DEFAULT_RELEASE_REPO) or DEFAULT_RELEASE_REPO).strip("/")
    url = f"{base}/repos/{repo}/releases/tags/{tag.name}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Omega",
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
    """The notes cut to MAX_NOTES_CHARS at a paragraph or line boundary."""
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
    """The prompt asking for the announcement, or "" when there are no notes."""
    notes = fit_notes((payload or {}).get("body"))
    if not notes:
        return ""
    return "\n".join((SUMMARY_INSTRUCTIONS,
                      "",
                      f"Call {TOOL_NAME} once, with the message as its {TOOL_ARGUMENT}.",
                      "",
                      f"VERSION: {tag.name}",
                      f"TITLE: {payload.get('name') or tag.name}",
                      f"PUBLISHED: {payload.get('published_at') or ''}",
                      f"LINK: {payload.get('html_url') or ''}",
                      "RELEASE NOTES:",
                      notes))


def _chat(prompt):
    """Ask core's LLM provider for the announcement and return its text.

    Core's providers answer with tool calls only, so the request offers one
    tool whose single argument is the message."""
    import providers
    tool = (providers.LLMTool().with_name(TOOL_NAME)
            .with_description("Send the release announcement to the users.")
            .add_parameter(providers.LLMToolParameter().with_name(TOOL_ARGUMENT)))
    request = (providers.LLMRequest()
               .add_message(providers.llmRequestMessage("user", prompt))
               .with_tools([tool]))
    response = providers.llmProviderChat(request)
    if response.is_error():
        raise RuntimeError(response.error)
    for call in response.calls:
        if call.name == TOOL_NAME:
            return str(call.arguments.get(TOOL_ARGUMENT, ""))
    return ""


def announcement(summary):
    """The summary as one channel message, line breaks written as \\n."""
    text = str(summary).strip()
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Telegram's markdown converter eats the blank line that ends a bullet list;
    # a zero-width space on that line holds the gap open.
    text = re.sub(r"(?m)^(- [^\n]*)\n\n+(?!- )", "\\1\n\u200b\n\n", text)
    return text.replace("\n", "\\n")


def mark_announced(tag):
    path = marker_path()
    try:
        path.write_text(f"{tag.name}\n", encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not record the announced release: {e}")
        return False
    logger.info(f"Announced release {tag.name}")
    return True


_tried = False


def announce():
    """Announce the configured release once per process; "" when nothing went out.

    Called from a heartbeat listener, so it runs on every loop iteration. Only
    the first call does any work."""
    global _tried
    if _tried:
        return ""
    _tried = True
    try:
        tag = ReleaseTag.configured()
        if tag is None:
            logger.info("No releaseTag is set, nothing to announce")
            return ""
        if tag.name == announced_release():
            logger.info(f"Release {tag.name} was announced already")
            return ""
        prompt = summary_request(tag, fetch_release(tag))
        if not prompt:
            return ""
        message = announcement(_chat(prompt))
        if not message:
            logger.warning("The release summary came back empty, announcing nothing")
            return ""
        import channels
        channels.commChannelSend(message)
        mark_announced(tag)
        return message
    except Exception as e:
        logger.exception(f"Giving up on the release announcement: {e}")
        return ""
