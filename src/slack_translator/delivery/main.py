"""Process entry point. The only module that reads the process environment (CFG)."""

import asyncio
import os
import sys

from pydantic import ValidationError

from slack_translator.delivery.adapters import AdaptersNotAvailable
from slack_translator.delivery.app import Overrides, create_app
from slack_translator.delivery.settings import Settings, invalid_keys

__all__ = ["Overrides", "create_app", "main"]


def main() -> None:
    try:
        settings = Settings.from_environ(os.environ)
    except ValidationError as error:
        sys.stderr.write(f"config_invalid: {', '.join(invalid_keys(error))}\n")
        raise SystemExit(1) from None

    try:
        application = create_app(settings)
    except AdaptersNotAvailable as error:
        sys.stderr.write(f"the {error} adapter is not built yet\n")
        raise SystemExit(2) from None

    try:
        asyncio.run(application.run())
    except Exception as error:
        sys.stderr.write(f"startup_failed: {type(error).__name__}\n")
        raise SystemExit(1) from None
