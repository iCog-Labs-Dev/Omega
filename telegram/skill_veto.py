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

A granted skill may also have a screen, which sees the call's text and may
refuse that one call. The channel screens remember against the profile's
forbidden memory categories.
"""
import json
import logging
import re

REFUSAL = "SKILL_DISABLED_IN_TELEGRAM"

# Core's own report of a line it could not read as a skill call. It is not a
# call, and it has to reach the model as it is.
_CORE_ERROR = "Error"


UNCHECKED = "SKILL_CALL_UNCHECKED"


def error_form(code, reason):
    """An (Error ...) form the loop hands back to the model."""
    return f"(Error {code} {json.dumps(reason)})"


def _refusal(skill):
    reason = (f"{skill} is not available in this chat. Nothing was run; "
              f"do not call it again.")
    return error_form(REFUSAL, reason)


def _unchecked(skill):
    reason = (f"{skill} could not be checked in a reply that did not parse. "
              f"Nothing was run; call it again on a line of its own.")
    return error_form(UNCHECKED, reason)


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


def _argument(form):
    """The text a one-argument call was given, unquoted when core quoted it."""
    rest = form[1:-1].strip()[len(_head(form)):].strip()
    if len(rest) >= 2 and rest.startswith('"') and rest.endswith('"'):
        try:
            return json.loads(rest, strict=False)
        except ValueError:
            return rest[1:-1]
    return rest


def refuse_ungranted_skills(sexprs, granted, commands=(), screens=None):
    """Replace each call to a skill not in granted with an error the model reads
    back. Everything else is left exactly as it was.

    commands is every skill name core knows, used only when the reply cannot be
    split into calls.

    screens maps a granted skill to a function given the call's text, which
    returns None to let the call run or an (Error ...) form to put in its
    place."""
    screens = screens or {}
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
        # A screened skill's text cannot be told apart here either, so a reply
        # naming one is refused the same way.
        names = set(re.findall(r'\(([^\s()"]+)', inner))
        named = sorted(name for name in names
                       if name in commands and name not in granted)
        if named:
            logging.warning(f"Refused a malformed reply that calls {', '.join(named)}")
            return "(" + _refusal(named[0]) + ")"
        screened = sorted(name for name in names if name in screens)
        if screened:
            logging.warning(f"Refused a malformed reply that calls {', '.join(screened)}")
            return "(" + _unchecked(screened[0]) + ")"
        return sexprs
    refused = []
    out = []
    for form in forms:
        skill = _head(form)
        if skill == _CORE_ERROR:
            out.append(form)
        elif skill not in granted:
            refused.append(skill)
            out.append(_refusal(skill))
        elif skill in screens and (verdict := screens[skill](_argument(form))):
            refused.append(skill)
            out.append(verdict)
        else:
            out.append(form)
    if not refused:
        return sexprs
    logging.warning(f"Refused skill calls: {', '.join(refused)}")
    return "(" + " ".join(out) + ")"


def install(granted, helper=None, screens=None):
    """Wrap core's reply parser so only granted skills reach evaluation, each
    screened skill only after its screen lets it through. A second call
    replaces the granted set and screens rather than wrapping twice."""
    if helper is None:
        import helper
    granted = frozenset(granted)
    screens = dict(screens or {})
    current = helper.balance_parentheses
    original = getattr(current, "_unwrapped", current)

    def balance_parentheses(s):
        return refuse_ungranted_skills(original(s), granted,
                                       getattr(helper, "LLM_COMMANDS", ()),
                                       screens)

    balance_parentheses._unwrapped = original
    balance_parentheses.__doc__ = original.__doc__
    helper.balance_parentheses = balance_parentheses
    logging.info(f"Skills granted in this channel: {', '.join(sorted(granted))}")
