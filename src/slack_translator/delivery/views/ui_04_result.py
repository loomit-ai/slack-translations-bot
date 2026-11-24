"""UI-04 and UI-05 · The private translation.

Reading order (UI-18): heading, then the complete text. A long translation is shown whole in
consecutive sections: Slack allows 50 blocks per message and 3,000 characters per section, so up to
144,000 characters fit, far more than a Slack message can carry as source. Only a translation
beyond that is cut, and the message then says how much is shown.

The top-level fallback text has its own limit (40,000 characters). Beyond it the fallback ends with
a pointer to the message, and the blocks still carry every character.
"""

from typing import Final

from slack_translator.domain.messages import PrivateMessage

SECTION_LIMIT: Final = 3000
MAX_RESULT_SECTIONS: Final = 48
MAX_SHOWN_CHARS: Final = SECTION_LIMIT * MAX_RESULT_SECTIONS
FALLBACK_LIMIT: Final = 39_000
FALLBACK_POINTER: Final = "\nThe full translation is shown in the message."


def split_sections(text: str, limit: int = SECTION_LIMIT) -> list[str]:
    """Split into chunks of at most `limit` characters that join back into `text` exactly.

    A chunk ends at a line break when it can, else at a space, else at the limit.
    """
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        window = rest[:limit]
        cut = window.rfind("\n") + 1 or window.rfind(" ") + 1 or limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    return chunks


def result_message(heading: str, translation: str) -> PrivateMessage:
    shown = translation
    note: str | None = None
    if len(translation) > MAX_SHOWN_CHARS:
        shown = split_sections(translation, MAX_SHOWN_CHARS)[0]
        note = (
            f"Showing the first {len(shown)} of {len(translation)} characters. "
            "The rest does not fit in one Slack message."
        )

    blocks: list[dict[str, object]] = [_section(f"*{heading}*")]
    blocks.extend(_section(chunk) for chunk in split_sections(shown) if chunk.strip())
    if note is not None:
        blocks.append(_section(note))
    return PrivateMessage(text=_fallback(heading, shown, note), blocks=blocks)


def _fallback(heading: str, shown: str, note: str | None) -> str:
    tail = f"\n{note}" if note is not None else ""
    text = f"{heading}\n{shown}{tail}"
    if len(text) <= FALLBACK_LIMIT:
        return text
    room = FALLBACK_LIMIT - len(heading) - len(FALLBACK_POINTER) - 2
    return f"{heading}\n{shown[:room]}…{FALLBACK_POINTER}"


def _section(text: str) -> dict[str, object]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}
