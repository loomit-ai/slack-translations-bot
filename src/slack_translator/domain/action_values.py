"""SLK-02 · The value carried by a Slack button, version 1.

It holds identifiers and language context, never message text. Slack passes it back on a click,
so it is untrusted input (REQ-09): `parse` accepts exactly this schema and nothing else.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Final, cast

from slack_translator.domain.languages import LanguageCode, Script

_VERSION: Final = 1
_CHANNEL = re.compile(r"^[CG][A-Z0-9]{8,}$")
_TS = re.compile(r"^\d{10}\.\d{6}$")
_LANGUAGE = re.compile(r"^[a-z]{2}$")
_KEYS: Final = frozenset({"v", "c", "ts", "tr", "l", "s"})
_SCRIPTS: Final[tuple[Script, ...]] = ("Hans", "Hant")


class ActionValueError(ValueError):
    """The button value is not a valid SLK-02 value. The message never contains the value."""


@dataclass(frozen=True, slots=True)
class ActionValue:
    channel: str
    ts: str
    thread_root_ts: str | None = None
    language: LanguageCode | None = None
    script: Script | None = None


def encode(value: ActionValue) -> str:
    data: dict[str, object] = {"v": _VERSION, "c": value.channel, "ts": value.ts}
    if value.thread_root_ts is not None:
        data["tr"] = value.thread_root_ts
    if value.language is not None:
        data["l"] = value.language
    if value.script is not None:
        data["s"] = value.script
    return json.dumps(data, separators=(",", ":"))


def parse(raw: str) -> ActionValue:
    try:
        loaded: object = json.loads(raw)
    except ValueError:
        raise ActionValueError("the button value is not JSON") from None
    if not isinstance(loaded, dict):
        raise ActionValueError("the button value has an unexpected shape")
    data = cast(dict[str, Any], loaded)
    if set(data) - _KEYS:
        raise ActionValueError("the button value has an unexpected shape")

    version = data.get("v")
    if type(version) is not int or version != _VERSION:
        raise ActionValueError("the button value has an unsupported version")

    channel = _text(data, "c", _CHANNEL, required=True)
    ts = _text(data, "ts", _TS, required=True)
    thread_root = _text(data, "tr", _TS)
    language = _text(data, "l", _LANGUAGE)
    script = _text(data, "s", None)

    if script is not None and (language != "zh" or script not in _SCRIPTS):
        raise ActionValueError("the button value has an invalid script")
    assert channel is not None and ts is not None
    return ActionValue(
        channel=channel,
        ts=ts,
        thread_root_ts=thread_root,
        language=LanguageCode(language) if language is not None else None,
        script="Hans" if script == "Hans" else "Hant" if script == "Hant" else None,
    )


def _text(
    data: dict[str, Any], key: str, pattern: re.Pattern[str] | None, *, required: bool = False
) -> str | None:
    if key not in data:
        if required:
            raise ActionValueError("the button value is missing a field")
        return None
    field = data[key]
    if not isinstance(field, str) or (pattern is not None and not pattern.match(field)):
        raise ActionValueError("the button value has an invalid field")
    return field
