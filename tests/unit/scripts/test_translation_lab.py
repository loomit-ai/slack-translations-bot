"""US-13 · The shared parts of the two terminal tools that try translations outside Slack."""

import csv
import importlib.util
import secrets
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"


def load_lab() -> ModuleType:
    spec = importlib.util.spec_from_file_location("translation_lab", SCRIPTS / "translation_lab.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["translation_lab"] = module
    spec.loader.exec_module(module)
    return module


lab = load_lab()

MINIMAL = {
    "TRANSLATOR_BASE_URL": "http://localhost:11434/v1",
    "TRANSLATOR_MODEL": "some-local-model",
    "TRANSLATOR_API_KEY": "local",
}


def write_env(folder: Path, values: dict[str, str], name: str = "local.env") -> Path:
    path = folder / name
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return path


class TestEnvFile:
    def test_pairs_comments_quotes_and_export_are_read(self, tmp_path: Path) -> None:
        path = tmp_path / "x.env"
        path.write_text(
            "# a comment\n"
            "\n"
            "TRANSLATOR_MODEL=plain\n"
            "export TRANSLATOR_API_KEY='quoted key'\n"
            'TRANSLATOR_BASE_URL="http://localhost:1234/v1"\n'
            'TRANSLATOR_EXTRA_BODY={"think": false, "a": "b=c"}\n'
            "no equals sign here\n",
            encoding="utf-8",
        )

        assert lab.read_env_file(path) == {
            "TRANSLATOR_MODEL": "plain",
            "TRANSLATOR_API_KEY": "quoted key",
            "TRANSLATOR_BASE_URL": "http://localhost:1234/v1",
            "TRANSLATOR_EXTRA_BODY": '{"think": false, "a": "b=c"}',
        }

    def test_a_missing_file_is_a_profile_error_naming_the_path(self, tmp_path: Path) -> None:
        with pytest.raises(lab.ProfileError, match="not found"):
            lab.read_env_file(tmp_path / "nope.env")


class TestProfile:
    def test_a_local_model_profile_needs_no_slack_tokens(self, tmp_path: Path) -> None:
        profile = lab.load_profile(write_env(tmp_path, MINIMAL, "my-local.env"))

        assert profile.name == "my-local"
        assert profile.model == "some-local-model"
        assert profile.host == "localhost"
        assert profile.settings.translator_extra_body == {}
        assert profile.settings.read_language == "en"

    def test_extra_body_and_timeout_come_from_the_file(self, tmp_path: Path) -> None:
        env = {**MINIMAL, "TRANSLATOR_EXTRA_BODY": '{"think": false}', "TRANSLATOR_TIMEOUT": "45"}

        profile = lab.load_profile(write_env(tmp_path, env))

        assert profile.settings.translator_extra_body == {"think": False}
        assert profile.settings.translator_timeout == 45

    def test_slack_values_in_the_file_are_ignored_not_needed_and_never_shown(
        self, tmp_path: Path
    ) -> None:
        token = f"xoxb-{secrets.token_hex(6)}"
        profile = lab.load_profile(write_env(tmp_path, {**MINIMAL, "SLACK_BOT_TOKEN": token}))

        assert token not in repr(profile)

    @pytest.mark.parametrize(
        ("changes", "key"),
        [
            ({"TRANSLATOR_BASE_URL": "http://{m}.example.com/v1"}, "TRANSLATOR_BASE_URL"),
            ({"TRANSLATOR_BASE_URL": "https://{m}.example.com/v1/"}, "TRANSLATOR_BASE_URL"),
            ({"TRANSLATOR_BASE_URL": "ftp://{m}.localhost/v1"}, "TRANSLATOR_BASE_URL"),
            ({"TRANSLATOR_MODEL": ""}, "TRANSLATOR_MODEL"),
            ({"TRANSLATOR_API_KEY": ""}, "TRANSLATOR_API_KEY"),
            ({"TRANSLATOR_EXTRA_BODY": '["{m}"]'}, "TRANSLATOR_EXTRA_BODY"),
            ({"TRANSLATOR_EXTRA_BODY": "not json {m}"}, "TRANSLATOR_EXTRA_BODY"),
            ({"TRANSLATOR_TIMEOUT": "0"}, "TRANSLATOR_TIMEOUT"),
        ],
    )
    def test_the_same_checks_as_the_bot_reject_a_bad_profile_naming_only_keys(
        self, tmp_path: Path, changes: dict[str, str], key: str
    ) -> None:
        marker = f"MARKER-{secrets.token_hex(6)}"
        env = {**MINIMAL, "TRANSLATOR_API_KEY": marker}
        env.update({name: value.replace("{m}", marker) for name, value in changes.items()})

        with pytest.raises(lab.ProfileError) as raised:
            lab.load_profile(write_env(tmp_path, env))

        assert key in str(raised.value)
        assert marker not in str(raised.value)

    def test_missing_keys_are_named(self, tmp_path: Path) -> None:
        with pytest.raises(lab.ProfileError) as raised:
            lab.load_profile(write_env(tmp_path, {"TRANSLATOR_MODEL": "x"}))

        assert "TRANSLATOR_BASE_URL" in str(raised.value)
        assert "TRANSLATOR_API_KEY" in str(raised.value)


class TestSlackTokenCheck:
    @pytest.mark.parametrize(
        "source",
        [
            "Hola <@U012ABCDE>, mira <#C012ABCDE|general>",
            "Aviso <!channel> importante",
            "Mira <https://example.com|esto> y <https://example.org>",
            "Gracias :tada: :+1:",
            "Usa `make check` aquí",
            "Mira:\n```\nprint(1)\n```\nlisto",
            "Sin nada especial",
        ],
    )
    def test_a_translation_that_keeps_every_token_is_clean(self, source: str) -> None:
        check = lab.check_slack_tokens(source, f"[kept] {source}")

        assert check.ok
        assert check.describe() == "ok"

    @pytest.mark.parametrize(
        ("source", "translation", "missing"),
        [
            ("Hola <@U012ABCDE>", "Hello there", ["<@U012ABCDE>"]),
            ("Hola <@U012ABCDE>", "Hello <@U012ABCDEF>", ["<@U012ABCDE>"]),
            ("Mira <https://x.com|esto>", "Look <https://x.com|this>", ["<https://x.com|esto>"]),
            ("Mira <#C012ABCDE|general>", "Look #general", ["<#C012ABCDE|general>"]),
            ("Gracias :tada:", "Thanks 🎉", [":tada:"]),
            ("Usa `make check`", "Use make check", ["`make check`"]),
            ("```\nx = 1\n```", "```\nx = 2\n```", ["```\nx = 1\n```"]),
            ("<@U012ABCDE> y <@U012ABCDE>", "<@U012ABCDE> and", ["<@U012ABCDE>"]),
        ],
    )
    def test_a_missing_or_changed_token_is_flagged(
        self, source: str, translation: str, missing: list[str]
    ) -> None:
        check = lab.check_slack_tokens(source, translation)

        assert not check.ok
        assert list(check.missing) == missing
        assert "missing" in check.describe()

    def test_a_control_sequence_the_model_added_is_flagged_as_added(self) -> None:
        check = lab.check_slack_tokens("Hola", "Hello <!channel> <https://evil.example|click>")

        assert list(check.missing) == []
        assert list(check.added) == ["<!channel>", "<https://evil.example|click>"]
        assert "added" in check.describe()

    def test_tokens_inside_a_code_block_belong_to_the_block(self) -> None:
        source = "```\nping <@U012ABCDE>\n```"

        assert lab.check_slack_tokens(source, source).ok
        assert not lab.check_slack_tokens(source, "```\nping someone\n```").ok


class TestReadMessages:
    def test_a_text_file_is_one_message_per_line_and_blank_lines_are_skipped(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "sample.txt"
        path.write_bytes("﻿파란 상자는 어디에 있나요?\r\n\r\n  \r\n두 번째 메시지\r\n".encode())

        assert lab.read_messages(path) == ["파란 상자는 어디에 있나요?", "두 번째 메시지"]

    @pytest.mark.parametrize("header", ["message", "Text", "SOURCE"])
    def test_a_csv_with_a_known_header_uses_that_column(self, tmp_path: Path, header: str) -> None:
        path = tmp_path / "sample.csv"
        path.write_text(f'id,{header},who\n1,hello there,ana\n2,"two\nlines",bo\n3,,cy\n')

        assert lab.read_messages(path) == ["hello there", "two\nlines"]

    def test_a_csv_without_a_known_header_uses_the_first_column_of_every_row(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "SAMPLE.CSV"
        path.write_text("first message,x\nsecond message,y\n")

        assert lab.read_messages(path) == ["first message", "second message"]

    def test_a_missing_file_and_an_empty_file_are_errors(self, tmp_path: Path) -> None:
        with pytest.raises(lab.ProfileError, match="not found"):
            lab.read_messages(tmp_path / "nope.txt")
        empty = tmp_path / "empty.txt"
        empty.write_text("\n\n")
        with pytest.raises(lab.ProfileError, match="no messages"):
            lab.read_messages(empty)


class TestResultsFile:
    def make_outcome(self, **changes: object) -> object:
        values: dict[str, object] = {
            "source": 'He said "hi", then\nleft <@U012ABCDE>',
            "translation": "그는 안녕이라고 말했다",
            "target": "ko",
            "seconds": 1.2345,
            "worked": True,
            "error": "",
            "check": lab.TokenCheck(missing=("<@U012ABCDE>",), added=()),
            "detected_code": "en",
            "detected_name": "English",
            "lead": 0.793,
            "decision": "no offer (team language)",
            "prompt_tokens": 120,
            "completion_tokens": 40,
        }
        values.update(changes)
        return lab.Outcome(**values)

    def test_the_file_has_one_row_per_message_and_empty_columns_for_the_human_rating(
        self, tmp_path: Path
    ) -> None:
        profile = lab.load_profile(write_env(tmp_path, MINIMAL, "local.env"))
        path = tmp_path / "out" / "results.csv"

        lab.write_results(
            path,
            [self.make_outcome(), self.make_outcome(worked=False, error="timeout", translation="")],
            profile,
        )

        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 2
        first, second = rows
        assert first["source"] == 'He said "hi", then\nleft <@U012ABCDE>'
        assert first["translation"] == "그는 안녕이라고 말했다"
        assert (first["target_language"], first["seconds"], first["worked"]) == (
            "ko",
            "1.23",
            "yes",
        )
        assert first["slack_tokens"] == "missing: <@U012ABCDE>"
        assert (first["detected_language"], first["lead"]) == ("en", "0.793")
        assert (first["model"], first["endpoint"]) == ("some-local-model", "localhost")
        assert first["rating"] == "" and first["notes"] == ""
        assert (second["worked"], second["error"]) == ("no", "timeout")

    def test_the_file_opens_in_a_spreadsheet_with_korean_intact(self, tmp_path: Path) -> None:
        profile = lab.load_profile(write_env(tmp_path, MINIMAL))
        path = tmp_path / "r.csv"

        lab.write_results(path, [self.make_outcome()], profile)

        assert path.read_bytes().startswith(b"\xef\xbb\xbf")
        assert "그는 안녕이라고 말했다" in path.read_text(encoding="utf-8-sig")


class TestDefaultResultsPath:
    def test_it_goes_into_the_ignored_lab_folder_named_after_sample_profile_and_target(
        self, tmp_path: Path
    ) -> None:
        from datetime import datetime

        profile = lab.load_profile(write_env(tmp_path, MINIMAL, "my-local.env"))

        path = lab.default_results_path(
            Path("lab/korean-sample.txt"), profile, "en", datetime(2026, 11, 24, 15, 30, 5)
        )

        assert path == REPO / "lab" / "results" / "korean-sample-my-local-en-20261124-153005.csv"


class TestPrivateFolder:
    def test_a_path_outside_the_repository_is_fine(self, tmp_path: Path) -> None:
        lab.ensure_private_path(tmp_path / "results.csv")

    def test_the_lab_folder_of_the_repository_is_ignored_and_allowed(self) -> None:
        lab.ensure_private_path(REPO / "lab" / "results" / "x.csv")

    def test_a_tracked_place_in_the_repository_is_refused(self) -> None:
        with pytest.raises(lab.ProfileError, match="not ignored by git"):
            lab.ensure_private_path(REPO / "docs" / "results.csv")


class TestRunAll:
    class StubRunner:
        def __init__(self, failing: set[str]) -> None:
            self.failing = failing
            self.seen: list[tuple[str, str]] = []

        async def run(self, text: str, target: str) -> object:
            self.seen.append((text, target))
            worked = text not in self.failing
            check = (
                lab.TokenCheck(missing=("<@U1>",), added=())
                if text == "flagged"
                else lab.TokenCheck((), ())
            )
            return lab.Outcome(
                source=text,
                translation=f"t:{text}" if worked else "",
                target=target,
                seconds=2.0 if worked else 1.0,
                worked=worked,
                error="" if worked else "timeout",
                check=check,
            )

    async def test_every_message_is_run_in_order_and_progress_is_reported_without_text(
        self,
    ) -> None:
        runner = self.StubRunner(failing={"b"})
        progress: list[tuple[int, int, bool]] = []

        outcomes = await lab.run_all(
            runner,
            ["a", "b", "c"],
            "ko",
            lambda number, total, outcome: progress.append((number, total, outcome.worked)),
        )

        assert runner.seen == [("a", "ko"), ("b", "ko"), ("c", "ko")]
        assert [o.worked for o in outcomes] == [True, False, True]
        assert progress == [(1, 3, True), (2, 3, False), (3, 3, True)]

    async def test_it_works_without_a_progress_callback(self) -> None:
        outcomes = await lab.run_all(self.StubRunner(set()), ["a"], "en")

        assert len(outcomes) == 1

    async def test_the_summary_counts_what_worked_failed_and_was_flagged(self) -> None:
        runner = self.StubRunner(failing={"b"})
        outcomes = await lab.run_all(runner, ["a", "b", "flagged"], "en")

        summary = lab.summarize(outcomes)

        assert (summary.total, summary.translated, summary.failed, summary.flagged) == (3, 2, 1, 1)
        assert summary.average_seconds == pytest.approx(5.0 / 3)
        assert summary.all_failed is False
        assert lab.summarize(await lab.run_all(self.StubRunner({"a"}), ["a"], "en")).all_failed
