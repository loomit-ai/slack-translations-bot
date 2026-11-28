"""UI-08 · The private error with Retry."""

from slack_translator.delivery.views.ui_08_failed import failed_message


def test_the_error_has_one_retry_button_that_carries_the_value() -> None:
    message = failed_message('{"v":1}')
    actions = [block for block in message.blocks if block["type"] == "actions"]
    assert len(actions) == 1
    [button] = actions[0]["elements"]
    assert button["action_id"] == "translate_retry"
    assert button["value"] == '{"v":1}'
    assert button["text"] == {"type": "plain_text", "text": "Retry"}
    assert button["accessibility_label"] == "Retry translation"


def test_the_fallback_names_the_failure_and_the_action() -> None:
    message = failed_message("{}")
    assert message.text == "We couldn’t translate this message. Retry translation."
    assert message.blocks[0]["text"]["text"] == "We couldn’t translate this message."
