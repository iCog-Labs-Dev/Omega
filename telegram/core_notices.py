"""Recognise the notices core sends on the model's behalf.

When the model spends its whole output budget on reasoning, or its request
times out, core writes a `send` itself with an explanation meant for whoever
runs the bot: raise maxOutputToken, lower reasoningMode, and so on. In a public
group that reads as noise, and core sends it on idle turns too. The channel
spots these notices so it can hand the details to admins and tell users only
that something went wrong.

Matching uses core's own text, imported from its provider library, so a change
to the wording in core cannot slip past:

    notice = recognise(text)
    if notice is not None:
        ...  # notice.kind is "token-limit" or "timeout"

A core without these notices (the fork core) has nothing to recognise.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CoreNotice:
    """A notice core sent, and which kind it is."""
    kind: str
    text: str


def _core_texts():
    """Core's empty-reply notice and the fixed start of its timeout notice,
    each None when this core has no such notice."""
    try:
        import lib_llm_ext
    except Exception:
        return None, None
    empty = getattr(lib_llm_ext, "LLM_EMPTY_RESPONSE_MESSAGE", None)
    timeout = getattr(lib_llm_ext, "LLM_TIMEOUT_MESSAGE", None)
    # The timeout notice is a template; everything before its first field is
    # fixed ("LLM request timed out at ").
    timeout_prefix = timeout.split("{", 1)[0] if isinstance(timeout, str) else None
    return empty, (timeout_prefix or None)


def recognise(text):
    """The CoreNotice `text` is, or None if it is anything else."""
    if not isinstance(text, str):
        return None
    # Core's send writes line breaks as a literal backslash-n.
    text = text.replace("\\n", "\n").strip()
    empty, timeout_prefix = _core_texts()
    if empty and text == empty.strip():
        return CoreNotice("token-limit", text)
    if timeout_prefix and text.startswith(timeout_prefix):
        return CoreNotice("timeout", text)
    return None
