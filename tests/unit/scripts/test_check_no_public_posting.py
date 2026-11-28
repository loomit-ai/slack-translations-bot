"""FF-02 must fail on a real public write and pass on private output."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_no_public_posting.py"


def run_guard(folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(folder)], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    "line",
    [
        "await client.chat_postMessage(channel=c, text=t)",
        "await client.chat_update(channel=c, ts=ts, text=t)",
        "await client.chat_scheduleMessage(channel=c, text=t, post_at=1)",
        "await client.files_upload_v2(channel=c, content=t)",
        "await say(text)",
        'payload = {"response_type": "in_channel"}',
    ],
)
def test_a_public_write_fails_the_build(tmp_path: Path, line: str) -> None:
    (tmp_path / "bad.py").write_text(f"async def f():\n    {line}\n")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "bad.py:2" in result.stderr


def test_private_output_passes(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text(
        "async def f():\n"
        "    await client.chat_postEphemeral(channel=c, user=u, text=t)\n"
        '    payload = {"response_type": "ephemeral"}\n'
        "    message = say_hello()\n"
    )
    assert run_guard(tmp_path).returncode == 0


def test_the_real_source_tree_passes() -> None:
    assert run_guard(SCRIPT.parents[1] / "src").returncode == 0
