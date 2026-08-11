#!/usr/bin/env python3
"""Turn a container's raw docker.log into something a human can read.

The raw log is a MeTTa interpreter trace: a few thousand lines of one-time import
boilerplate, then one entry per loop iteration where the model's action is wrapped in
nested (RESPONSE: (RESULTS: ((COMMAND_RETURN: ... text, and every string in it carries
"_quote_"/"_newline_"/"_apostrophe_" in place of the character those names describe (the
loop's own escaping, so a string survives MeTTa's parser — see src/utils.metta). The
whole accumulated prompt also gets re-logged every tick, since the loop rebuilds it from
scratch each time. Nothing here changes what an agent does or reads; it only makes the
record of what happened legible after the fact.

    bench/clean_log.py path/to/docker.log            # print to stdout
    bench/clean_log.py path/to/trial-1/               # write a .clean.log next to each
    docker logs -f <container> | bench/clean_log.py -   # filter a live stream as it arrives
"""

import argparse
import sys
from pathlib import Path

import re

UNESCAPE = [("_quote_", '"'), ("_newline_", "\n"), ("_apostrophe_", "'")]

# Component tags (the "loop" in "... | INFO | loop | ...") worth keeping. Everything else
# (policy, config, memory, plugin, channels, sentence_transformers, ...) is one-time setup
# noise that never changes across a trial.
KEEP_COMPONENTS = {"loop", "openai", "anthropic", "openrouter", "asicloud", "asione"}

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| *\w+ *\| *(\S+) *\| (.*)$")


def unescape(text):
    for token, real in UNESCAPE:
        text = text.replace(token, real)
    return text


def _balanced(text, start):
    """Index just past the paren that closes the one opened at `start`, honoring quotes."""
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def unwrap_response(message):
    """Pull the actual command out of (RESPONSE: (RESULTS: ((COMMAND_RETURN: ...))))...).

    A best-effort text strip, not a MeTTa parser: anything that doesn't match this exact
    shape is returned unescaped but otherwise as-is, rather than guessed at.
    """
    marker = "COMMAND_RETURN:"
    idx = message.find(marker)
    if idx < 0:
        m = re.match(r"^\(RESPONSE: (.*)\)$", message)
        return m.group(1) if m else message
    start = idx + len(marker)
    while start < len(message) and message[start] == " ":
        start += 1
    if start >= len(message) or message[start] != "(":
        return message
    end = _balanced(message, start)
    return message[start:end]


class Cleaner:
    """Line-by-line cleaner, so the same logic reads a whole file or filters a live stream.

    feed() takes one raw log line (no trailing newline) and returns its cleaned form, or
    None if the line should be dropped. State is just the startup-skip count, so a live
    `docker logs -f` stream can be filtered exactly like a finished file, one line as it
    arrives instead of the whole thing at once.
    """

    def __init__(self):
        self._seen_first = False
        self._skipped = 0

    def feed(self, line):
        m = LINE_RE.match(line)
        prefix = ""
        if not self._seen_first:
            if not m:
                self._skipped += 1
                return None
            self._seen_first = True
            if self._skipped:
                prefix = f"[omitted {self._skipped} lines of MeTTa interpreter startup]\n"
        if not m:
            return None  # nginx access/error lines: no timestamp prefix in this format
        ts, component, message = m.groups()
        if component not in KEEP_COMPONENTS:
            return prefix or None
        message = unescape(message)
        if message.startswith("(---------iteration"):
            return prefix + f"\n--- {ts}  {message.strip('()- ')} ---"
        if message.startswith("(CHARS_SENT:"):
            return prefix or None  # the full rebuilt prompt; unchanged parts are in the role file
        if message.startswith("(RESPONSE:"):
            return prefix + f"{ts}  {unwrap_response(message)}"
        return prefix + f"{ts}  [{component}] {message}"


def clean(raw):
    cleaner = Cleaner()
    out = [cleaned for cleaned in (cleaner.feed(line) for line in raw.splitlines())
           if cleaned is not None]
    return "\n".join(out) + "\n"


def follow(lines):
    """Filter an already-open line iterator (e.g. sys.stdin) and print each result as it comes."""
    cleaner = Cleaner()
    for line in lines:
        cleaned = cleaner.feed(line.rstrip("\n"))
        if cleaned is not None:
            print(cleaned, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path,
                        help="a docker.log file, a directory to clean every docker.log "
                             "under, or - to filter stdin as a live stream")
    args = parser.parse_args()

    if str(args.path) == "-":
        follow(sys.stdin)
    elif args.path.is_dir():
        logs = sorted(args.path.rglob("docker.log"))
        if not logs:
            sys.exit(f"no docker.log files under {args.path}")
        for log in logs:
            out_path = log.with_suffix(".clean.log")
            out_path.write_text(clean(log.read_text(errors="ignore")))
            print(f"{log} -> {out_path}")
    else:
        sys.stdout.write(clean(args.path.read_text(errors="ignore")))


if __name__ == "__main__":
    main()
