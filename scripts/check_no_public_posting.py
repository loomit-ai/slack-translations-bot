"""FF-02 · Fail if code under the given folders can post publicly with the bot token.

ADR-006 and REQ-06: the bot only shows private output. These names are the ways to post to a
channel, edit a posted message, schedule one or upload a file. Any appearance fails the build,
including in comments, so nobody can keep a "temporary" public write around.

Usage: python scripts/check_no_public_posting.py [folder ...]   (default: src)
"""

import re
import sys
from pathlib import Path

FORBIDDEN = re.compile(
    r"chat_postMessage|chat_update|chat_scheduleMessage|files_upload|\bsay\s*\(|in_channel"
)


def find_violations(roots: list[Path]) -> list[str]:
    violations: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                match = FORBIDDEN.search(line)
                if match:
                    violations.append(f"{path}:{number}: forbidden '{match.group(0)}' (FF-02)")
    return violations


def main(argv: list[str]) -> int:
    roots = [Path(arg) for arg in argv] or [Path("src")]
    violations = find_violations(roots)
    for violation in violations:
        sys.stderr.write(violation + "\n")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
