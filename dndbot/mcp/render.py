"""Telegram HTML -> Markdown and plain text.

Everything the bot says is Telegram-flavoured HTML - messages, help text, even
the text of some exceptions. The MCP server speaks Markdown to the model, so
every string leaving a tool passes through this module first. It is the only
place that knows both dialects.

Order matters: tags are converted while they are still tags, and HTML entities
(``&amp;``, ``&lt;``) are decoded last, so player text that was escaped for
Telegram does not get mistaken for markup on the way out.
"""

from __future__ import annotations

import html as entities
import re
from typing import Any

_PRE = re.compile(r"<pre>(.*?)</pre>", re.I | re.S)
_CODE = re.compile(r"<code>(.*?)</code>", re.I | re.S)
_LINK = re.compile(r'<a\s+href="([^"]+)">(.*?)</a>', re.I | re.S)
_QUOTE = re.compile(r"<blockquote>(.*?)</blockquote>", re.I | re.S)
_BOLD = re.compile(r"</?(?:b|strong)>", re.I)
_ITALIC = re.compile(r"</?(?:i|em)>", re.I)
_STRIKE = re.compile(r"</?(?:s|strike|del)>", re.I)
_HIDDEN = re.compile(r"</?(?:tg-spoiler|u)>", re.I)
_BREAK = re.compile(r"<br\s*/?>", re.I)
_PARA = re.compile(r"</(?:p|div|li|tr|h[1-6])>", re.I)
_TAG = re.compile(r"<[^>]+>")

# Characters that would change meaning if a player typed them in a name.
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>])")


def strip(text: Any) -> str:
    """Drop every tag and decode the entities: plain text.

    Used for error messages, which the model reads as-is.
    """
    plain = _BREAK.sub("\n", str(text))
    plain = _PARA.sub("\n", plain)
    plain = _TAG.sub("", plain)
    return entities.unescape(plain).strip()


def to_markdown(text: Any) -> str:
    """Convert Telegram HTML to Markdown.

    ``<b>``/``<i>``/``<code>``/``<a>``/``<blockquote>``/``<pre>`` become their
    Markdown equivalents and any other tag is dropped, which is what the
    SRD formatters emit for the odd construct.
    """
    out = str(text)
    out = _PRE.sub(lambda m: "```\n" + m.group(1).strip() + "\n```", out)
    out = _CODE.sub(lambda m: "`" + m.group(1) + "`", out)
    out = _LINK.sub(lambda m: "[" + m.group(2) + "](" + m.group(1) + ")", out)
    out = _QUOTE.sub(
        lambda m: "\n".join("> " + line for line in m.group(1).splitlines()),
        out,
    )
    out = _BOLD.sub("**", out)
    out = _ITALIC.sub("*", out)
    out = _STRIKE.sub("~~", out)
    out = _HIDDEN.sub("", out)
    out = _BREAK.sub("\n", out)
    out = _PARA.sub("\n", out)
    out = _TAG.sub("", out)
    out = entities.unescape(out)
    return "\n".join(line.rstrip() for line in out.strip().splitlines())


def esc_md(text: Any) -> str:
    """Escape a player-supplied name so Markdown cannot reinterpret it.

    Campaign names, character names and notes come from players; without this,
    ``Gandalf *the Grey*`` would silently become emphasis and ``<test>`` would
    look like markup. Input is raw text, not HTML - convert HTML first with
    :func:`strip` or :func:`to_markdown`.
    """
    return _MD_SPECIAL.sub(r"\\\1", str(text if text is not None else ""))
