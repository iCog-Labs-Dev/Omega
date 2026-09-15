"""Shared paragraph/line splitting for Telegram text and speech."""
import re

TELEGRAM_TEXT_LIMIT = 4096


def _pack(parts, separator, fits):
    """Rejoin consecutive parts for as long as the result still fits."""
    run = ""
    for part in parts:
        candidate = f"{run}{separator}{part}" if run else part
        if fits(candidate):
            run = candidate
            continue
        if run:
            yield run
        run = part
    if run:
        yield run


def _hard_cut(text, fits):
    """Slice a run with no boundary left to break on, shrinking each slice until
    it fits. Rendering only grows text, so a raw 4096 characters is the ceiling
    worth trying first."""
    while text:
        take = min(len(text), TELEGRAM_TEXT_LIMIT)
        while take > 1 and not fits(text[:take]):
            take = take * 3 // 4
        if take < len(text):
            sentences = list(re.finditer(r"[.!?]\s+|[。！？]\s*", text[:take]))
            spaces = list(re.finditer(r"\s+", text[:take]))
            boundaries = sentences or spaces
            if boundaries:
                take = boundaries[-1].end()
        yield text[:take]
        text = text[take:]


def split_for_telegram(text, fits=None):
    """Break text into pieces that each fit in one Telegram message.

    Cuts on the largest boundary that fits - a blank line first, then a single
    line, then mid-line as a last resort - so a long answer arrives as readable
    paragraphs instead of arbitrary slices. Text that already fits comes back
    unchanged, as one piece.

        split_for_telegram("short")     # ["short"]
        split_for_telegram("a" * 9000)  # three pieces, in order

    `fits` decides whether one piece can be sent, and defaults to counting raw
    characters. The channel passes the rendered length instead, because that is
    what Telegram measures.

    Pieces holding nothing but whitespace are dropped: Telegram rejects a blank
    message, and that refusal is not worth queueing.
    """
    if fits is None:
        def fits(piece):
            return len(piece) <= TELEGRAM_TEXT_LIMIT

    pieces = []
    for paragraph in _pack(text.split("\n\n"), "\n\n", fits):
        if fits(paragraph):
            pieces.append(paragraph)
            continue
        for line in _pack(paragraph.split("\n"), "\n", fits):
            if fits(line):
                pieces.append(line)
            else:
                pieces.extend(_hard_cut(line, fits))
    return [piece for piece in pieces if piece.strip()]
