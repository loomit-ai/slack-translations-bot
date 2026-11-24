"""REQ-07 and REQ-08 · What counts as language, and how model output is shown.

Pure functions. The translator always receives the original text; only detection sees the
cleaned text.
"""

import html
import re
import unicodedata
from typing import Final

MIN_LETTERS: Final = 3

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_SLACK_TOKEN = re.compile(r"<[^<>\n]*>")
_BARE_URL = re.compile(r"https?://\S+")
_EMOJI_CODE = re.compile(r":[A-Za-z+_][A-Za-z0-9_+\-]*:")
_CONTROL_TOKEN = re.compile(r"<(?:[@#!]|https?:|mailto:|tel:)[^<>\n]*>")


def clean_for_detection(text: str) -> str:
    """Remove mentions, links, emoji codes and code, then collapse whitespace (REQ-07)."""
    cleaned = _CODE_BLOCK.sub(" ", text)
    cleaned = _INLINE_CODE.sub(" ", cleaned)
    cleaned = _SLACK_TOKEN.sub(" ", cleaned)
    cleaned = _BARE_URL.sub(" ", cleaned)
    cleaned = _EMOJI_CODE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    return " ".join(cleaned.split())


def letter_count(text: str) -> int:
    """Characters in a Unicode letter category, in any script."""
    return sum(1 for char in text if unicodedata.category(char).startswith("L"))


def neutralize_controls(translated: str, source: str) -> str:
    """Show a Slack control sequence the model added as plain characters (REQ-08).

    A mention, channel or link that appears character for character in the source stays active,
    because the translator rules preserve those. Anything else is escaped, so a customer cannot
    plant a link or `@channel` in the member's private result.
    """

    def escape_if_new(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in source:
            return token
        return token.replace("<", "&lt;").replace(">", "&gt;")

    return _CONTROL_TOKEN.sub(escape_if_new, translated)
