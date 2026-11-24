"""UI-03, UI-06, UI-07, UI-09 and the no-text sentence: one section, identical fallback (UI-18)."""

from typing import Literal

from slack_translator.domain.messages import PrivateMessage


def _notice(sentence: str, kind: Literal["mrkdwn", "plain_text"] = "mrkdwn") -> PrivateMessage:
    return PrivateMessage(
        text=sentence,
        blocks=[{"type": "section", "text": {"type": kind, "text": sentence}}],
    )


def progress_message() -> PrivateMessage:
    return _notice("Translating…", "plain_text")


def error_notice_message() -> PrivateMessage:
    return _notice("An error has occurred…", "plain_text")


def already_read_message(reading_language: str) -> PrivateMessage:
    return _notice(f"This message is already in {reading_language}.")


def unavailable_message() -> PrivateMessage:
    return _notice("This message is no longer available.")


def not_allowed_message() -> PrivateMessage:
    return _notice("Translation is available only in approved channels.")


def no_text_message() -> PrivateMessage:
    return _notice("This message has no text to translate.")
