"""Process entry point. The only module that reads the process environment (CFG)."""

import os
import sys

from pydantic import ValidationError

from slack_translator.delivery.settings import Settings, invalid_keys


def main() -> None:
    try:
        Settings.from_environ(os.environ)
    except ValidationError as error:
        sys.stderr.write(f"config_invalid: {', '.join(invalid_keys(error))}\n")
        raise SystemExit(1) from None

    sys.stderr.write("settings are valid; the Slack wiring is not built yet (milestone 0.1)\n")
    raise SystemExit(2)
