"""markdown_render.to_markdownv2 must not let any link hide where it points.

A reply relays a stranger's text - a QR code, a PDF, a web result - so a link in
it must show its real target. Markdown spells links many ways; the ones below all
render to the same masked [label](url) in MarkdownV2, so the reveal has to work on
the parsed link, not the raw text. Each case asserts the rendered output carries
no text_link entity whose visible text differs from where it goes.
"""
import os
import sys

import pytest

pytest.importorskip("telegramify_markdown")
from telegramify_markdown import convert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from markdown_render import to_markdownv2

EVIL = "https://evil.example"


def masked_link(markdownv2):
    """The (visible text, target) of a link whose target is hidden, or None.

    Re-parses the rendered output: a surviving mask is a text_link entity whose
    covered text is not its url."""
    text, entities = convert(markdownv2)
    units = text.encode("utf-16-le")
    for e in entities:
        if e.type == "text_link" and e.url:
            shown = units[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
            if shown != e.url:
                return shown, e.url
    return None


@pytest.mark.parametrize("markdown", [
    f"[https://safe.example/login]({EVIL}/steal)",   # plain inline link
    f"[Safe [site] here]({EVIL})",                    # brackets inside the label
    rf"[safe\]]({EVIL})",                             # an escaped bracket in the label
    f"[Singularity\nNET]({EVIL})",                    # a line break in the label
    f"[SingularityNET][1]\n\n[1]: {EVIL}",            # a reference link
    f"[SingularityNET]\n\n[SingularityNET]: {EVIL}",  # a shortcut reference link
])
def test_no_link_form_can_hide_its_target(markdown):
    assert masked_link(to_markdownv2(markdown)) is None


def test_the_target_is_shown_to_the_reader():
    out = to_markdownv2(f"[SingularityNET]({EVIL})")
    assert "SingularityNET" in out and "evil" in out  # the dot is MarkdownV2-escaped


def test_a_link_that_already_shows_its_target_is_not_doubled():
    out = to_markdownv2(f"[{EVIL}]({EVIL})")
    assert out.count("evil") == 1


def test_a_code_block_is_left_as_written():
    """The rewrite must not touch a link the agent is only showing as an example."""
    out = to_markdownv2("```\n[a](https://b.example)\n```")
    assert "[a](https://b.example)" in out


def test_formatting_around_a_link_still_lands_right():
    """Revealing a target lengthens the text; a bold run after it must still wrap
    exactly `bold` (a wrong offset shift would spread its `*` markers)."""
    out = to_markdownv2(f"[x]({EVIL}) then **bold** end")
    assert "*bold*" in out and out.index("*bold*") > out.index("evil")
