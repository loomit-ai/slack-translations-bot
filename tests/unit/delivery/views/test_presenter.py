"""ReadPresenter · every outcome maps to its Design layout."""

from slack_translator.delivery.views.presenter import SlackReadPresenter
from slack_translator.domain.messages import (
    AlreadyInReadLanguage,
    ChannelNotAllowed,
    ErrorNotice,
    MessageUnavailable,
    NothingToTranslate,
    Translated,
    Translating,
    TranslationFailed,
)

presenter = SlackReadPresenter(read_language_name="English")


def test_a_translation_naming_its_source() -> None:
    message = presenter.present(Translated("Where is the blue box?", "Korean"))
    assert message.text == "English translation from Korean\nWhere is the blue box?"


def test_a_translation_without_a_known_source_names_none() -> None:
    message = presenter.present(Translated("Please bring the blue box.", None))
    assert message.text == "English translation\nPlease bring the blue box."


def test_the_other_outcomes() -> None:
    assert presenter.present(Translating()).text == "Translating…"
    assert presenter.present(ErrorNotice()).text == "An error has occurred…"
    assert presenter.present(AlreadyInReadLanguage("English")).text == (
        "This message is already in English."
    )
    assert presenter.present(NothingToTranslate()).text == "This message has no text to translate."
    assert presenter.present(MessageUnavailable()).text == "This message is no longer available."
    assert presenter.present(ChannelNotAllowed()).text == (
        "Translation is available only in approved channels."
    )


def test_a_failure_carries_the_retry_value() -> None:
    message = presenter.present(TranslationFailed(retry_value="VALUE"))
    assert message.blocks[1]["elements"][0]["value"] == "VALUE"
