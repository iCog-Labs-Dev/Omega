"""Refuse every skill the channel profile does not grant.

In five days of production the Telegram bot ran 13,906 shell commands, pip
installs, background jobs and a git push with a token it found on disk, steered
by group members. The profile forbade all of it, but nothing read that part,
and core asks the channel for a veto only before websearch.

Core turns the model's reply into skill calls in one place, the Python function
helper.balance_parentheses, and the loop reaches it through py-call, which looks
the function up on every call. Wrapping it is the one point where a call to a
skill the channel does not grant can be stopped before it is evaluated, without
a core change. A stopped call becomes an (Error ...) form, which the loop hands
back to the model as ALERT_FAILED, so the model learns the skill is off instead
of retrying.

Taking the names out of helper.LLM_COMMANDS would not do: core refuses to remove
its built-in commands, and a line it no longer recognises as a command is glued
onto the command above it, so "shell ls" would be posted to the chat as the end
of a send.

The skills granted come from the profile's callable_capabilities; see
capabilities.py.
"""
import json
import logging
import re

REFUSAL = "SKILL_DISABLED_IN_TELEGRAM"

# Core's own report of a line it could not read as a skill call. It is not a
# call, and it has to reach the model as it is.
_CORE_ERROR = "Error"


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


def refuse_ungranted_skills(sexprs, granted, commands=()):
    """Replace each call to a skill not in granted with an error the model reads
    back. Everything else is left exactly as it was.

    commands is every skill name core knows, used only when the reply cannot be
    split into calls."""
    if not (isinstance(sexprs, str) and sexprs.startswith("(") and sexprs.endswith(")")):
        # Not a list of calls. The loop reports it to the model as malformed
        # and evaluates nothing.
        return sexprs
    inner = sexprs[1:-1]
    forms = _top_level_forms(inner)
    if forms is None:
        # Core could not quote something cleanly. Whatever MeTTa makes of it,
        # it must not be a skill the channel refuses, so a reply naming one is
        # refused whole; anything else is left for the loop to report as before.
        named = sorted({name for name in re.findall(r'\(([^\s()"]+)', inner)
                        if name in commands and name not in granted})
        if not named:
            return sexprs
        logging.warning(f"Refused a malformed reply that calls {', '.join(named)}")
        return "(" + _refusal(named[0]) + ")"
    refused = []
    out = []
    for form in forms:
        skill = _head(form)
        if skill != _CORE_ERROR and skill not in granted:
            refused.append(skill)
            out.append(_refusal(skill))
        else:
            out.append(form)
    if not refused:
        return sexprs
    logging.warning(f"Refused skills the profile does not grant: {', '.join(refused)}")
    return "(" + " ".join(out) + ")"


def install(granted, helper=None):
    """Wrap core's reply parser so only granted skills reach evaluation. A
    second call replaces the granted set rather than wrapping twice."""
    if helper is None:
        import helper
    granted = frozenset(granted)
    current = helper.balance_parentheses
    original = getattr(current, "_unwrapped", current)

    def balance_parentheses(s):
        return refuse_ungranted_skills(original(s), granted,
                                       getattr(helper, "LLM_COMMANDS", ()))

    balance_parentheses._unwrapped = original
    balance_parentheses.__doc__ = original.__doc__
    helper.balance_parentheses = balance_parentheses
    logging.info(f"Skills granted in this channel: {', '.join(sorted(granted))}")
