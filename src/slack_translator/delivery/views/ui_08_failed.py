"""UI-08 · The private error with Retry (SLK-01: action_id translate_retry)."""

from slack_translator.domain.messages import PrivateMessage

RETRY_ACTION_ID = "translate_retry"


def failed_message(retry_value: str) -> PrivateMessage:
    sentence = "We couldn’t translate this message."
    return PrivateMessage(
        text=f"{sentence} Retry translation.",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": sentence}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": RETRY_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Retry"},
                        "accessibility_label": "Retry translation",
                        "value": retry_value,
                    }
                ],
            },
        ],
    )
