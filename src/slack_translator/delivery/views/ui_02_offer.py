"""UI-02 · The private offer to read a customer message (SLK-01: action_id translate_offer).

It names the language and carries identifiers in the button value, never the message text.
"""

from slack_translator.domain.messages import PrivateMessage

OFFER_ACTION_ID = "translate_offer"


def offer_message(source_language: str, reading_language: str, action_value: str) -> PrivateMessage:
    heading = f"Message in {source_language}"
    return PrivateMessage(
        text=f"{heading}. Translate this message into {reading_language}.",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{heading}*"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": OFFER_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Translate this message"},
                        "accessibility_label": f"Translate this message into {reading_language}",
                        "value": action_value,
                    }
                ],
            },
        ],
    )
