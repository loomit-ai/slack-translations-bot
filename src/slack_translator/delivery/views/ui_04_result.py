"""UI-04 and UI-05 · The private translation.

Reading order (UI-18): heading, then the complete text. A long translation is shown whole in
consecutive sections. Only a translation that cannot fit in one Slack message is cut, and the
message then says how much is shown.
"""

from typing import Final

from slack_translator.domain.messages import PrivateMessage

SECTION_LIMIT: Final = 3000
MAX_SHOWN_CHARS: Final = 36_000


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
    fallback = f"{heading}\n{shown}"
    if note is not None:
        blocks.append(_section(note))
        fallback = f"{fallback}\n{note}"
    return PrivateMessage(text=fallback, blocks=blocks)


def _section(text: str) -> dict[str, object]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}
