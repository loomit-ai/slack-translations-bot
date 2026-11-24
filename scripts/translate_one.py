"""US-13 · Translate one message with the model named in an env file, outside Slack.

    uv run python scripts/translate_one.py --env lab/local.env --text "Where is the box?"
    echo "Hola <@U012ABCDE>" | uv run python scripts/translate_one.py --env lab/local.env -t ko

The message is the `--text` argument, or standard input, or typed at a prompt. It shows the
translation, the model, the endpoint host and the time taken, the language Lingua detects and its
lead, and whether any Slack mention, link, emoji code or code block was lost or changed. One
attempt, no retry, so what you see is what the model did. Exit code 0 on success, 1 when the
translation failed, 2 when something you can fix is wrong (env file, target, message).
"""

import argparse
import asyncio
import sys
from pathlib import Path

from translation_lab import (
    Lab,
    Outcome,
    ProfileError,
    load_profile,
    use_utf8_output,
    validate_target,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translate_one.py", description="Translate one message outside Slack (US-13)."
    )
    parser.add_argument("--env", required=True, type=Path, help="env file with the model settings")
    parser.add_argument(
        "--target", "-t", help="target language code (default: the reading language)"
    )
    parser.add_argument("--text", help="the message (default: standard input, or a prompt)")
    parser.add_argument("--hint", default="auto", help="auto (like the bot), none, or a code")
    parser.add_argument("--no-detect", action="store_true", help="skip Lingua")
    return parser


def read_text(given: str | None) -> str:
    if given is not None:
        return given
    if sys.stdin.isatty():
        return input("Message: ")
    return sys.stdin.read().rstrip("\n")


def render(outcome: Outcome, model: str, host: str, tokens: bool = True) -> str:
    facts = (
        f"Model: {model} · Endpoint: {host} · Target: {outcome.target} · "
        f"Time: {outcome.seconds:.2f} s"
    )
    if tokens and outcome.prompt_tokens is not None:
        facts += f" · Tokens: {outcome.prompt_tokens} in, {outcome.completion_tokens} out"
    lines = [facts]
    if outcome.decision:
        if outcome.detected_name is not None and outcome.lead is not None:
            lines.append(
                f"Detected: {outcome.detected_name} ({outcome.detected_code}), "
                f"lead {outcome.lead:.2f} · {outcome.decision}"
            )
        else:
            lines.append(f"Detected: nothing certain · {outcome.decision}")
    lines.append(f"Slack tokens: {outcome.check.describe()}")
    lines.append("-" * 40)
    lines.append(outcome.translation)
    return "\n".join(lines)


async def run(arguments: argparse.Namespace) -> int:
    profile = load_profile(arguments.env)
    target = validate_target(arguments.target or profile.settings.read_language)
    text = read_text(arguments.text)
    if not text.strip():
        raise ProfileError("there is no message to translate")

    lab = Lab(profile, detect=not arguments.no_detect, hint=arguments.hint)
    await lab.start()
    try:
        outcome = await lab.run(text, target)
    finally:
        await lab.aclose()

    if not outcome.worked:
        status = f" (HTTP {outcome.http_status})" if outcome.http_status else ""
        sys.stderr.write(
            f"Translation failed: {outcome.error}{status} · Model: {profile.model} · "
            f"Endpoint: {profile.host} · Time: {outcome.seconds:.2f} s\n"
        )
        return 1
    sys.stdout.write(render(outcome, profile.model, profile.host) + "\n")
    return 0


def main(argv: list[str]) -> int:
    use_utf8_output()
    arguments = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(arguments))
    except (ProfileError, ValueError) as problem:
        sys.stderr.write(f"{problem}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
