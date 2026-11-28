"""UI-04 and UI-05 · The private translation.

Reading order (UI-18): heading, then the complete text. A long translation is shown whole in
consecutive sections: Slack allows 50 blocks per message and 3,000 characters per section, so up to
144,000 characters fit, far more than a Slack message can carry as source. The budget is counted in
blocks, not characters: only a translation that needs more than 48 sections is cut, and the
message then says how much is shown.

The translation text is `verbatim` (REQ-08): Slack then leaves bare URLs, channel names and
mentions as plain text instead of turning them into live ones.

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

    A chunk ends at a line break when one falls in its second half, else at a space there, else
    at the limit. That keeps chunks nearly full, so the number of blocks stays close to
    `len(text) / limit` however the line breaks fall.
    """
    chunks: list[str] = []
    rest = text
    floor = limit // 2
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        window = rest[:limit]
        newline = window.rfind("\n") + 1
        space = window.rfind(" ") + 1
        cut = newline if newline > floor else space if space > floor else limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    return chunks


def result_message(heading: str, translation: str) -> PrivateMessage:
    chunks = split_sections(translation)
    shown = translation
    note: str | None = None
    if len(chunks) > MAX_RESULT_SECTIONS:
        chunks = chunks[:MAX_RESULT_SECTIONS]
        shown = "".join(chunks)
        note = (
            f"Showing the first {len(shown)} of {len(translation)} characters. "
            "The rest does not fit in one Slack message."
        )

    blocks: list[dict[str, object]] = [_section(f"*{heading}*")]
    blocks.extend(_section(chunk, verbatim=True) for chunk in chunks if chunk.strip())
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


def _section(text: str, *, verbatim: bool = False) -> dict[str, object]:
    block: dict[str, object] = {"type": "mrkdwn", "text": text}
    if verbatim:
        block["verbatim"] = True
    return {"type": "section", "text": block}
