"""ReadPresenter · Turns an outcome into the Block Kit message the Design spec names."""

from slack_translator.delivery.views.notices import (
    already_read_message,
    error_notice_message,
    no_text_message,
    not_allowed_message,
    progress_message,
    unavailable_message,
)
from slack_translator.delivery.views.ui_02_offer import offer_message
from slack_translator.delivery.views.ui_04_result import result_message
from slack_translator.delivery.views.ui_08_failed import failed_message
from slack_translator.domain.messages import (
    AlreadyInReadLanguage,
    ChannelNotAllowed,
    ErrorNotice,
    MessageUnavailable,
    NothingToTranslate,
    Offer,
    PrivateMessage,
    ReadOutcome,
    Translated,
    Translating,
    TranslationFailed,
)


class SlackReadPresenter:
    def __init__(self, read_language_name: str) -> None:
        self._read_language = read_language_name

    def present(self, outcome: ReadOutcome) -> PrivateMessage:
        match outcome:
            case Offer(source_language=source, reading_language=reading, action_value=value):
                return offer_message(source, reading, value)
            case Translating():
                return progress_message()
            case ErrorNotice():
                return error_notice_message()
            case Translated(text=text, source_language=source):
                heading = f"{self._read_language} translation"
                if source is not None:
                    heading = f"{heading} from {source}"
                return result_message(heading, text)
            case AlreadyInReadLanguage(language=language):
                return already_read_message(language)
            case NothingToTranslate():
                return no_text_message()
            case MessageUnavailable():
                return unavailable_message()
            case TranslationFailed(retry_value=value):
                return failed_message(value)
            case ChannelNotAllowed():
                return not_allowed_message()
