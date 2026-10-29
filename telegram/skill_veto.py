"""Keep the skills a public chat must not reach out of the model's hands.

In five days of production the Telegram bot ran 13,906 shell commands, pip
installs, background jobs and a git push with a token it found on disk, steered
by group members. The channel profile forbids code execution and file writes,
but nothing reads that section, and core asks the channel for a veto only before
websearch.

Core turns the model's reply into skill calls in one place, the Python function
helper.balance_parentheses, and the loop reaches it through py-call, which looks
the function up on every call. Wrapping it is the one point where a call to a
forbidden skill can be stopped before it is evaluated, without a core change.
A stopped call becomes an (Error ...) form, which the loop hands back to the
model as ALERT_FAILED, so the model learns the skill is off instead of retrying.

Taking the names out of helper.LLM_COMMANDS would not do: core refuses to remove
its built-in commands, and a line it no longer recognises as a command is glued
onto the command above it, so "shell ls" would be posted to the chat as the end
of a send.
"""
import json
import logging

DISABLED_SKILLS = frozenset({
    "shell",
    "write-file",
    "write-file-b64",
    "append-file",
    "metta",
})

REFUSAL = "SKILL_DISABLED_IN_TELEGRAM"


def _refusal(skill):
    reason = (f"{skill} is not available in this chat. Nothing was run; "
              f"do not call it again.")
    return f"(Error {REFUSAL} {json.dumps(reason)})"


def _top_level_forms(s):
    """Split a run of s-expressions into its top-level forms, or None if it is
    not one. Quoted strings may hold parentheses and escaped quotes."""
    forms = []
    depth = 0
    start = None
    in_string = False
    escaped = False
    for i, ch in enumerate(s):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            if depth == 0:
                return None
            in_string = True
        elif ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                forms.append(s[start:i + 1])
        elif depth == 0 and not ch.isspace():
            return None
    if depth != 0 or in_string:
        return None
    return forms


def _head(form):
    parts = form[1:].split(maxsplit=1)
    return parts[0].rstrip(")") if parts else ""


def refuse_disabled_skills(sexprs):
    """Replace each call to a disabled skill in core's parsed reply with an
    error the model reads back. Everything else is left exactly as it was."""
    if not (isinstance(sexprs, str) and sexprs.startswith("(") and sexprs.endswith(")")):
        # Not a list of calls. The loop reports it to the model as malformed
        # and evaluates nothing.
        return sexprs
    inner = sexprs[1:-1]
    forms = _top_level_forms(inner)
    if forms is None:
        # Core could not quote something cleanly. Whatever MeTTa makes of it,
        # it must not be a disabled skill, so a reply naming one is refused
        # whole; anything else is left for the loop to report as before.
        named = [s for s in sorted(DISABLED_SKILLS) if f"({s} " in inner or f"({s})" in inner]
        if not named:
            return sexprs
        logging.warning(f"Refused a malformed reply that calls {', '.join(named)}")
        return "(" + _refusal(named[0]) + ")"
    refused = []
    out = []
    for form in forms:
        skill = _head(form)
        if skill in DISABLED_SKILLS:
            refused.append(skill)
            out.append(_refusal(skill))
        else:
            out.append(form)
    if not refused:
        return sexprs
    logging.warning(f"Refused disabled skills: {', '.join(refused)}")
    return "(" + " ".join(out) + ")"


def install(helper=None):
    """Wrap core's reply parser so disabled skills are refused. Safe to call
    more than once."""
    if helper is None:
        import helper
    current = helper.balance_parentheses
    if getattr(current, "_refuses_disabled_skills", False):
        return

    def balance_parentheses(s):
        return refuse_disabled_skills(current(s))

    balance_parentheses._refuses_disabled_skills = True
    balance_parentheses.__doc__ = current.__doc__
    helper.balance_parentheses = balance_parentheses
    logging.info(f"Disabled skills in this channel: {', '.join(sorted(DISABLED_SKILLS))}")
