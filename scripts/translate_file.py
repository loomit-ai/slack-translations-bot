"""US-13 · Translate a file of messages with the model named in an env file, outside Slack.

    uv run python scripts/translate_file.py --env lab/local.env lab/sample.txt --target en

The sample is one message per line (.txt) or a CSV (a `message`, `text` or `source` column, else
the first). Every message is translated once; a failed one becomes a row, not a stop. The results
file has the source, the translation, the target, the time, whether it worked, the Slack token
check, Lingua's language and lead, and empty `rating` (usable, needs edits, wrong) and `notes`
columns for you. Results go to lab/results/ unless `--out` says otherwise, and a place inside the
repository that git does not ignore is refused. Progress and the summary never print message text.
Exit code 0 when the file was written, 1 when every message failed, 2 when something you can fix
is wrong.
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from translation_lab import (
    Lab,
    Outcome,
    ProfileError,
    default_results_path,
    ensure_private_path,
    load_profile,
    read_messages,
    use_utf8_output,
    validate_target,
    write_results,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translate_file.py", description="Translate a file of messages outside Slack (US-13)."
    )
    parser.add_argument("sample", type=Path, help="one message per line, or a CSV")
    parser.add_argument("--env", required=True, type=Path, help="env file with the model settings")
    parser.add_argument("--target", help="target language code (default: the reading language)")
    parser.add_argument("--out", type=Path, help="results file (default: under lab/results/)")
    parser.add_argument("--hint", default="auto", help="auto (like the bot), none, or a code")
    parser.add_argument("--no-detect", action="store_true", help="skip Lingua")
    return parser


def summary(outcomes: list[Outcome], model: str, host: str, path: Path) -> str:
    worked = [o for o in outcomes if o.worked]
    failed = len(outcomes) - len(worked)
    flagged = sum(1 for o in worked if not o.check.ok)
    average = sum(o.seconds for o in outcomes) / len(outcomes)
    return (
        f"{len(worked)} of {len(outcomes)} translated · {failed} failed · "
        f"{flagged} with Slack token flags · average {average:.2f} s · {model} at {host}\n"
        f"Results: {path}\n"
        "Fill the rating column with usable, needs edits or wrong, and use notes for anything else."
    )


async def run(arguments: argparse.Namespace) -> int:
    profile = load_profile(arguments.env)
    target = validate_target(arguments.target or profile.settings.read_language)
    messages = read_messages(arguments.sample)
    out = arguments.out or default_results_path(arguments.sample, profile, target, datetime.now())
    ensure_private_path(out)

    lab = Lab(profile, detect=not arguments.no_detect, hint=arguments.hint)
    await lab.start()
    outcomes: list[Outcome] = []
    try:
        for number, message in enumerate(messages, start=1):
            outcome = await lab.run(message, target)
            outcomes.append(outcome)
            state = "ok" if outcome.worked else f"failed ({outcome.error})"
            flag = "" if outcome.check.ok else " · Slack token flag"
            sys.stderr.write(f"[{number}/{len(messages)}] {state} {outcome.seconds:.1f} s{flag}\n")
    finally:
        await lab.aclose()

    write_results(out, outcomes, profile)
    sys.stdout.write(summary(outcomes, profile.model, profile.host, out) + "\n")
    return 1 if not any(o.worked for o in outcomes) else 0


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
