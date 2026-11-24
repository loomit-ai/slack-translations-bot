"""UI-03, UI-06, UI-07, UI-09 and the no-text sentence: one section, identical fallback (UI-18)."""

from slack_translator.domain.messages import PrivateMessage


def _notice(sentence: str) -> PrivateMessage:
    return PrivateMessage(
        text=sentence,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": sentence}}],
    )


def progress_message() -> PrivateMessage:
    return _notice("Translating…")


def already_read_message(reading_language: str) -> PrivateMessage:
    return _notice(f"This message is already in {reading_language}.")


def unavailable_message() -> PrivateMessage:
    return _notice("This message is no longer available.")


def not_allowed_message() -> PrivateMessage:
    return _notice("Translation is available only in approved channels.")


def no_text_message() -> PrivateMessage:
    return _notice("This message has no text to translate.")
