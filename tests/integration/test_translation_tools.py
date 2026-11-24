"""US-13 · The two terminal tools, run as real commands against the fake translator (TS-02)."""

import asyncio
import csv
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tests.support.fake_translator import Answer, Fail, FakeTranslatorServer

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"

KOREAN = "파란 상자에 종이 별이 세 개 있어요."
ENGLISH = "I put three paper stars in the blue box."
SLACKY = "Hola <@U012ABCDE>, mira <#C012ABCDE|general> y <https://x.com|esto> :tada: `ok`"


def write_profile(folder: Path, fake: FakeTranslatorServer, model: str = "test-model") -> Path:
    path = folder / f"{model}.env"
    path.write_text(
        f"TRANSLATOR_BASE_URL={fake.base_url}\n"
        f"TRANSLATOR_MODEL={model}\n"
        "TRANSLATOR_API_KEY=local\n"
        "TRANSLATOR_TIMEOUT=5\n",
        encoding="utf-8",
    )
    return path


async def run_tool(
    script: str, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )

    return await asyncio.to_thread(run)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
async def fake() -> AsyncIterator[FakeTranslatorServer]:
    async with FakeTranslatorServer() as server:
        yield server


class TestOneMessage:
    async def test_it_shows_the_translation_the_model_the_endpoint_and_the_time(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("The blue box is on the table."))

        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--text", KOREAN,
            "--no-detect",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        assert "The blue box is on the table." in done.stdout
        assert "test-model" in done.stdout
        assert "127.0.0.1" in done.stdout
        assert "Target: en" in done.stdout
        assert " s" in done.stdout
        [request] = fake.requests
        assert request.user_text == KOREAN
        assert "English" in request.system_message

    async def test_a_message_can_be_piped_in_and_reaches_the_model_unchanged(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--no-detect",
            stdin=f"{SLACKY}\n",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        assert fake.requests[0].user_text == SLACKY

    async def test_the_target_language_is_a_parameter_so_english_to_korean_works(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("파란 상자는 탁자 위에 있어요."))

        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--target", "ko",
            "--text", ENGLISH, "--no-detect",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        assert "Target: ko" in done.stdout
        assert "파란 상자는 탁자 위에 있어요." in done.stdout
        assert "Korean" in fake.requests[0].system_message

    async def test_it_shows_the_language_lingua_detects_and_its_lead(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        profile = str(write_profile(tmp_path, fake))

        korean = await run_tool("translate_one.py", "--env", profile, "--text", KOREAN)
        english = await run_tool("translate_one.py", "--env", profile, "--text", ENGLISH)

        assert korean.returncode == 0 and english.returncode == 0, korean.stderr + english.stderr
        assert "Korean (ko)" in korean.stdout and "lead 1.00" in korean.stdout
        assert "offer" in korean.stdout
        assert "English (en)" in english.stdout
        assert "team language" in english.stdout

    async def test_it_flags_a_slack_token_the_model_dropped_but_still_shows_the_translation(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("Hello there, look at this."))

        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--text", SLACKY,
            "--no-detect",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        assert "Hello there, look at this." in done.stdout
        assert "missing" in done.stdout
        assert "<@U012ABCDE>" in done.stdout

    async def test_a_clean_translation_says_the_tokens_are_fine(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer(f"Hi {SLACKY}"))

        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--text", SLACKY,
            "--no-detect",
        )  # fmt: skip

        assert "Slack tokens: ok" in done.stdout

    async def test_a_failing_model_gives_the_error_kind_and_never_the_providers_body(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.set_default(Fail(500, echo_input=True))

        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--text", KOREAN,
            "--no-detect",
        )  # fmt: skip

        assert done.returncode == 1
        output = done.stdout + done.stderr
        assert "unavailable" in output and "500" in output
        assert "fake provider error" not in output

    async def test_a_bad_profile_stops_with_key_names_and_never_values(
        self, tmp_path: Path
    ) -> None:
        env = tmp_path / "bad.env"
        env.write_text(
            "TRANSLATOR_BASE_URL=http://example.com/v1?SECRET-MARKER\nTRANSLATOR_MODEL=m\n"
            "TRANSLATOR_API_KEY=SECRET-MARKER\n"
        )

        done = await run_tool("translate_one.py", "--env", str(env), "--text", KOREAN)

        assert done.returncode == 2
        assert "TRANSLATOR_BASE_URL" in done.stderr
        assert "SECRET-MARKER" not in done.stdout + done.stderr

    async def test_a_target_that_is_not_a_language_code_is_refused_before_any_call(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--target",
            "korean please", "--text", KOREAN, "--no-detect",
        )  # fmt: skip

        assert done.returncode == 2
        assert fake.requests == []


class TestDiagnose:
    """`--diagnose` sends one fixed made-up request and shows what the provider itself says."""

    async def test_it_shows_the_status_and_the_providers_own_error_message(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.set_default(Fail(404))

        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--diagnose",
        )  # fmt: skip

        assert done.returncode == 1
        assert "HTTP 404" in done.stdout
        assert "fake provider error" in done.stdout
        assert "test-model" in done.stdout and "127.0.0.1" in done.stdout

    async def test_it_sends_only_its_own_fixed_text_never_a_message_of_the_person(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        done = await run_tool(
            "translate_one.py", "--env", str(write_profile(tmp_path, fake)), "--diagnose",
            "--text", "SECRET-MARKER a real customer sentence",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        [request] = fake.requests
        assert "SECRET-MARKER" not in request.raw_body.decode()
        assert "SECRET-MARKER" not in done.stdout + done.stderr
        assert "HTTP 200" in done.stdout

    async def test_it_sends_the_extra_body_of_the_profile_because_routing_rules_decide_the_answer(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        profile = write_profile(tmp_path, fake)
        profile.write_text(
            profile.read_text()
            + 'TRANSLATOR_EXTRA_BODY={"provider": {"data_collection": "deny"}}\n'
        )

        await run_tool("translate_one.py", "--env", str(profile), "--diagnose")

        assert fake.requests[0].body["provider"] == {"data_collection": "deny"}
        assert fake.requests[0].body["model"] == "test-model"

    async def test_a_server_that_cannot_be_reached_is_reported_not_crashed(
        self, tmp_path: Path
    ) -> None:
        async with FakeTranslatorServer() as gone:
            profile = write_profile(tmp_path, gone)

        done = await run_tool("translate_one.py", "--env", str(profile), "--diagnose")

        assert done.returncode == 1
        assert "could not connect" in done.stdout.lower()


class TestAFileOfMessages:
    async def test_every_line_is_translated_and_the_results_file_leaves_the_rating_empty(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        sample = tmp_path / "sample.txt"
        sample.write_text(f"{KOREAN}\n\n{SLACKY}\n세 번째 메시지입니다\n", encoding="utf-8")
        out = tmp_path / "results.csv"

        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)), str(sample),
            "--out", str(out), "--no-detect",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        rows = read_rows(out)
        assert [r["source"] for r in rows] == [KOREAN, SLACKY, "세 번째 메시지입니다"]
        assert all(r["worked"] == "yes" and r["translation"] for r in rows)
        assert all(r["target_language"] == "en" and r["model"] == "test-model" for r in rows)
        assert all(r["rating"] == "" and r["notes"] == "" for r in rows)
        assert [request.user_text for request in fake.requests] == [r["source"] for r in rows]
        assert "3 of 3 translated" in done.stdout

    async def test_a_failed_message_is_a_row_not_a_stop(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("one"), Fail(401), Answer("three"))
        sample = tmp_path / "sample.txt"
        sample.write_text("first message\nsecond message\nthird message\n")
        out = tmp_path / "results.csv"

        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)), str(sample),
            "--out", str(out), "--no-detect",
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        first, second, third = read_rows(out)
        assert (first["worked"], second["worked"], third["worked"]) == ("yes", "no", "yes")
        assert second["error"] == "rejected" and second["translation"] == ""
        assert "2 of 3 translated" in done.stdout and "1 failed" in done.stdout

    async def test_when_every_message_fails_the_exit_code_says_so_and_the_file_is_still_written(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.set_default(Fail(401))
        sample = tmp_path / "sample.txt"
        sample.write_text("first message\nsecond message\n")
        out = tmp_path / "results.csv"

        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)), str(sample),
            "--out", str(out), "--no-detect",
        )  # fmt: skip

        assert done.returncode == 1
        assert [r["worked"] for r in read_rows(out)] == ["no", "no"]

    async def test_the_checks_and_the_detection_land_in_the_row(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("Hello there."))
        sample = tmp_path / "sample.csv"
        sample.write_text(f'id,message\n1,"{SLACKY} 그리고 파란 상자는 어디에 있나요?"\n')
        out = tmp_path / "results.csv"

        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)), str(sample),
            "--out", str(out),
        )  # fmt: skip

        assert done.returncode == 0, done.stderr
        [row] = read_rows(out)
        assert row["slack_tokens"].startswith("missing:")
        assert row["detected_language"] != "" and row["lead"] != ""
        assert row["bot_decision"] != ""

    async def test_neither_progress_nor_summary_prints_any_message_text(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("TRANSLATED-MARKER one"), Fail(500, echo_input=True))
        sample = tmp_path / "sample.txt"
        sample.write_text("SOURCE-MARKER one\nSOURCE-MARKER two\n")
        out = tmp_path / "results.csv"

        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)), str(sample),
            "--out", str(out), "--no-detect",
        )  # fmt: skip

        output = done.stdout + done.stderr
        assert "SOURCE-MARKER" not in output and "TRANSLATED-MARKER" not in output
        assert "fake provider error" not in output

    async def test_two_profiles_side_by_side_differ_only_by_their_env_file(
        self, tmp_path: Path
    ) -> None:
        sample = tmp_path / "sample.txt"
        sample.write_text("first message\n")
        async with FakeTranslatorServer() as local, FakeTranslatorServer() as hosted:
            local.enqueue(Answer("from the local one"))
            hosted.enqueue(Answer("from the hosted one"))
            for name, server in (("local-model", local), ("hosted-model", hosted)):
                await run_tool(
                    "translate_file.py", "--env", str(write_profile(tmp_path, server, name)),
                    str(sample), "--out", str(tmp_path / f"{name}.csv"), "--no-detect",
                )  # fmt: skip

        [local_row] = read_rows(tmp_path / "local-model.csv")
        [hosted_row] = read_rows(tmp_path / "hosted-model.csv")
        assert (local_row["model"], local_row["translation"]) == (
            "local-model",
            "from the local one",
        )
        assert (hosted_row["model"], hosted_row["translation"]) == (
            "hosted-model",
            "from the hosted one",
        )

    async def test_results_are_refused_in_a_place_git_would_track(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        sample = tmp_path / "sample.txt"
        sample.write_text("first message\n")
        tracked = REPO / "docs" / "should-never-exist.csv"

        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)), str(sample),
            "--out", str(tracked), "--no-detect",
        )  # fmt: skip

        assert done.returncode == 2
        assert not tracked.exists()
        assert fake.requests == []

    async def test_a_missing_sample_file_stops_before_any_call(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        done = await run_tool(
            "translate_file.py", "--env", str(write_profile(tmp_path, fake)),
            str(tmp_path / "nope.txt"), "--out", str(tmp_path / "r.csv"), "--no-detect",
        )  # fmt: skip

        assert done.returncode == 2
        assert fake.requests == []
