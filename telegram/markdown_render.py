"""Render a reply to Telegram MarkdownV2 without letting a link hide its target.

MarkdownV2 draws ``[label](target)`` as a tap on ``label`` that opens ``target``,
so a reply relaying a stranger's text - a QR code, a PDF, a web result - can show
a trusted-looking label over a hostile target. Markdown has many ways to spell a
link (inline, reference, shortcut, brackets or escapes inside the label), and a
regex over the raw text misses most of them, so the reveal works on the parsed
links instead: telegramify resolves every form to a ``text_link`` entity that
carries the real destination, which this appends to the visible text.

    to_markdownv2("[SingularityNET](https://evil.example)")
    # -> "SingularityNET \\(https://evil\\.example\\)"  (no tappable mask)

A code block is a ``pre``/``code`` entity, never ``text_link``, so examples the
agent shows are left exactly as written.
"""

try:
    from telegramify_markdown import convert, entities_to_markdownv2
    _AVAILABLE = True
except ImportError:  # the dependency is optional at import time
    _AVAILABLE = False


def _u16(text):
    """Length of `text` in UTF-16 code units, the unit Telegram entity offsets use."""
    return len(text.encode("utf-16-le")) // 2


def to_markdownv2(markdown):
    """MarkdownV2 for `markdown` with every link's real target shown in the text.

    A link whose visible text already is its target is left alone; any other link
    becomes ``label (target)`` as plain text, so nothing a tap opens is hidden.
    """
    if not _AVAILABLE:
        return markdown

    text, entities = convert(markdown)
    links = sorted((e for e in entities if e.type == "text_link" and e.url),
                   key=lambda e: e.offset)
    if not links:
        return entities_to_markdownv2(text, entities)

    units = text.encode("utf-16-le")

    def py_index(offset_u16):
        return len(units[:offset_u16 * 2].decode("utf-16-le"))

    parts, inserts, prev = [], [], 0
    for link in links:
        end = py_index(link.offset + link.length)
        display = text[py_index(link.offset):end]
        parts.append(text[prev:end])
        shown = "" if display == link.url else f" ({link.url})"
        parts.append(shown)
        inserts.append((link.offset + link.length, _u16(shown)))
        prev = end
    parts.append(text[prev:])
    revealed = "".join(parts)

    # The links become plain text; shift the entities kept around them so their
    # offsets still line up with the text the reveal has lengthened.
    kept = [e for e in entities if not (e.type == "text_link" and e.url)]
    for e in kept:
        start, stop = e.offset, e.offset + e.length
        e.offset += sum(n for pos, n in inserts if pos <= start)
        e.length += sum(n for pos, n in inserts if start < pos < stop)
    return entities_to_markdownv2(revealed, kept)
