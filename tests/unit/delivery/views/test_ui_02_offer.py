"""UI-02 · The private offer."""

from slack_translator.delivery.views.ui_02_offer import OFFER_ACTION_ID, offer_message


def test_the_offer_is_a_heading_and_one_button_with_the_design_wording() -> None:
    message = offer_message("Korean", "English", "VALUE")

    assert message.text == "Message in Korean. Translate this message into English."
    heading, actions = message.blocks
    assert heading["text"]["text"] == "*Message in Korean*"
    [button] = actions["elements"]
    assert button["action_id"] == OFFER_ACTION_ID == "translate_offer"
    assert button["text"] == {"type": "plain_text", "text": "Translate this message"}
    assert button["accessibility_label"] == "Translate this message into English"
    assert button["value"] == "VALUE"


def test_the_offer_never_contains_the_message_text() -> None:
    message = offer_message("Korean", "English", '{"v":1}')
    assert "{" not in message.text
