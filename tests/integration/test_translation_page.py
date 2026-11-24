"""US-13 · The local translation page, driven the way a browser drives it, against the fake
translator (TS-02). One group runs the real command as a process; the others start the same server
in this process so a test can reach into the fake."""

import asyncio
import csv
import logging
import re
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from tests.support.fake_translator import Answer, Fail, FakeTranslatorServer, Hang, Malformed
from tests.support.scripts import SCRIPTS, load_script

lab = load_script("translation_lab")
page = load_script("translation_page")

REPO = SCRIPTS.parent
KOREAN = "파란 상자에 종이 별이 세 개 있어요."
ENGLISH = "I put three paper stars in the blue box."
SLACKY = "Hola <@U012ABCDE>, mira <#C012ABCDE|general> y <https://x.com|esto> :tada: `ok`"
SECRET_KEY = "local-secret-key"


def write_env(
    folder: Path, base_url: str, *, model: str = "test-model", timeout: str = "5"
) -> Path:
    path = folder / f"{model}.env"
    path.write_text(
        f"TRANSLATOR_BASE_URL={base_url}\nTRANSLATOR_MODEL={model}\n"
        f"TRANSLATOR_API_KEY={SECRET_KEY}\nTRANSLATOR_TIMEOUT={timeout}\n",
        encoding="utf-8",
    )
    return path


def files_under(folder: Path) -> set[Path]:
    return set(folder.rglob("*")) if folder.exists() else set()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class Served:
    server: Any
    session: aiohttp.ClientSession
    fake: FakeTranslatorServer
    results: Path
    env_name: str

    async def get(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        async with self.session.get(path, **kwargs) as response:
            return response.status, await response.json()

    async def post(self, path: str, payload: object, **kwargs: Any) -> tuple[int, Any]:
        async with self.session.post(path, json=payload, **kwargs) as response:
            return response.status, await response.json()

    async def translate(self, text: str, target: str = "en") -> tuple[int, Any]:
        return await self.post("/api/translate", {"text": text, "target": target})

    async def start_file(
        self, content: str, name: str = "sample.txt", target: str = "en"
    ) -> tuple[int, Any]:
        return await self.post("/api/file", {"name": name, "content": content, "target": target})

    async def job(self, after: int = 0) -> dict[str, Any]:
        _, body = await self.get(f"/api/job?after={after}")
        return body["job"]

    async def first_row(self) -> None:
        for _ in range(400):
            if (await self.job())["rows"]:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("no message was translated")

    async def finished_job(self) -> dict[str, Any]:
        for _ in range(400):
            job = await self.job()
            if job["state"] != "running":
                return job
            await asyncio.sleep(0.05)
        raise AssertionError("the job did not finish")


@pytest.fixture
async def fake() -> AsyncIterator[FakeTranslatorServer]:
    async with FakeTranslatorServer() as server:
        yield server


async def serve(
    fake: FakeTranslatorServer, folder: Path, *, detect: bool = False, patience: str = "5"
) -> AsyncIterator[Served]:
    env = write_env(folder, fake.base_url, timeout=patience)
    results = folder / "results"
    server = page.PageServer(
        lab.load_profile(env), env_name=env.name, port=0, detect=detect, results_dir=results
    )
    async with server, aiohttp.ClientSession(base_url=server.url) as session:
        yield Served(server, session, fake, results, env.name)


@pytest.fixture
async def served(fake: FakeTranslatorServer, tmp_path: Path) -> AsyncIterator[Served]:
    async for value in serve(fake, tmp_path):
        yield value


@pytest.fixture
async def detecting(fake: FakeTranslatorServer, tmp_path: Path) -> AsyncIterator[Served]:
    async for value in serve(fake, tmp_path, detect=True):
        yield value


@pytest.fixture
async def impatient(fake: FakeTranslatorServer, tmp_path: Path) -> AsyncIterator[Served]:
    async for value in serve(fake, tmp_path, patience="1"):
        yield value


class Command:
    """The real command as a process, with everything it prints kept."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.seen: list[str] = []
        self.url = ""

    async def wait_for_address(self) -> None:
        assert self.process.stdout is not None
        async with asyncio.timeout(60):
            while True:
                line = (await self.process.stdout.readline()).decode()
                if not line:
                    raise AssertionError("the command ended: " + "".join(self.seen))
                self.seen.append(line)
                found = re.search(r"http://127\.0\.0\.1:\d+", line)
                if found:
                    self.url = found.group(0)
                    return

    async def stop(self) -> str:
        if self.process.returncode is None:
            self.process.terminate()
        out, err = await self.process.communicate()
        return "".join(self.seen) + out.decode() + err.decode()


async def start_command(*args: str) -> Command:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SCRIPTS / "translation_page.py"),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return Command(process)


async def run_command(*args: str) -> tuple[int, str]:
    command = await start_command(*args)
    try:
        async with asyncio.timeout(60):
            await command.process.wait()
    finally:
        output = await command.stop()
    return command.process.returncode or 0, output


class TestTheCommand:
    async def test_it_translates_one_message_and_one_file_and_saves_the_marked_results(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("The blue box is on the table."), Answer("one"), Answer("two"))
        env = write_env(tmp_path, fake.base_url)
        results = tmp_path / "results"
        command = await start_command(
            "--env", str(env), "--port", "0", "--no-detect", "--results-dir", str(results)
        )
        try:
            await command.wait_for_address()
            async with aiohttp.ClientSession(base_url=command.url) as session:
                async with session.get("/") as response:
                    assert response.status == 200
                    assert "<h1" in await response.text()
                async with session.get("/api/info") as response:
                    info = await response.json()
                assert info["model"] == "test-model"
                assert info["where"] == "stays on this computer"

                async with session.post(
                    "/api/translate", json={"text": KOREAN, "target": "en"}
                ) as response:
                    one = await response.json()
                assert one["ok"] and one["translation"] == "The blue box is on the table."

                async with session.post(
                    "/api/file",
                    json={"name": "small.txt", "content": f"{KOREAN}\n{ENGLISH}\n", "target": "en"},
                ) as response:
                    assert response.status == 202
                    started = await response.json()
                job: dict[str, Any] = {"state": "running", "rows": []}
                for _ in range(200):
                    async with session.get("/api/job?after=0") as response:
                        job = (await response.json())["job"]
                    if job["state"] != "running":
                        break
                    await asyncio.sleep(0.05)
                assert [row["translation"] for row in job["rows"]] == ["one", "two"]

                marks = [{"rating": "usable", "note": ""}, {"rating": "wrong", "note": "mixed up"}]
                async with session.post(
                    "/api/job/save", json={"id": started["id"], "marks": marks}
                ) as response:
                    assert response.status == 200
        finally:
            output = await command.stop()

        [saved] = list(results.glob("small-*.csv"))
        rows = read_rows(saved)
        assert [(r["translation"], r["rating"], r["notes"]) for r in rows] == [
            ("one", "usable", ""),
            ("two", "wrong", "mixed up"),
        ]
        assert KOREAN not in output and ENGLISH not in output

    @pytest.mark.parametrize(
        "host", ["0.0.0.0", "localhost", "::1", "127.0.0.2", "192.168.1.20", ""]
    )
    async def test_it_refuses_to_listen_on_anything_but_127_0_0_1(
        self, fake: FakeTranslatorServer, tmp_path: Path, host: str
    ) -> None:
        env = write_env(tmp_path, fake.base_url)

        code, output = await run_command("--env", str(env), "--host", host, "--port", "0")

        assert code == 2
        assert "127.0.0.1" in output and "only" in output.lower()

    async def test_a_port_that_is_taken_is_explained(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        env = write_env(tmp_path, fake.base_url)
        taken = await asyncio.start_server(lambda *_: None, "127.0.0.1", 0)
        try:
            port = taken.sockets[0].getsockname()[1]

            code, output = await run_command("--env", str(env), "--port", str(port), "--no-detect")
        finally:
            taken.close()

        assert code == 2
        assert str(port) in output and "already in use" in output and "PORT=" in output

    async def test_a_bad_env_file_stops_with_key_names_and_never_values(
        self, tmp_path: Path
    ) -> None:
        env = tmp_path / "bad.env"
        env.write_text(f"TRANSLATOR_BASE_URL=http://localhost:1\nTRANSLATOR_API_KEY={SECRET_KEY}\n")

        code, output = await run_command("--env", str(env), "--port", "0")

        assert code == 2
        assert "TRANSLATOR_MODEL" in output and SECRET_KEY not in output

    async def test_results_are_refused_in_a_place_git_would_track(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        env = write_env(tmp_path, fake.base_url)

        code, output = await run_command(
            "--env", str(env), "--port", "0", "--results-dir", str(REPO / "docs")
        )

        assert code == 2
        assert "not ignored by git" in output

    async def test_no_message_text_reaches_what_it_prints_even_when_the_provider_echoes_it(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        fake.enqueue(Answer("TRANSLATED-MARKER"), Fail(500, echo_input=True))
        env = write_env(tmp_path, fake.base_url)
        command = await start_command(
            "--env", str(env), "--port", "0", "--no-detect", "--results-dir", str(tmp_path / "r")
        )
        try:
            await command.wait_for_address()
            async with aiohttp.ClientSession(base_url=command.url) as session:
                for text in ("SOURCE-MARKER one", "SOURCE-MARKER two"):
                    async with session.post("/api/translate", json={"text": text}) as response:
                        assert response.status == 200
        finally:
            output = await command.stop()

        assert "SOURCE-MARKER" not in output and "TRANSLATED-MARKER" not in output
        assert "fake provider error" not in output


class TestWhatTheBrowserCanReach:
    async def test_the_server_listens_on_127_0_0_1_and_on_nothing_else(
        self, served: Served
    ) -> None:
        assert served.server.addresses == [("127.0.0.1", served.server.port)]
        assert served.server.url == f"http://127.0.0.1:{served.server.port}"

    async def test_the_page_loads_nothing_from_the_internet_and_the_browser_is_told_so(
        self, served: Served
    ) -> None:
        for path in ("/", "/app.js", "/app.css"):
            async with served.session.get(path) as response:
                body = await response.text()
                policy = response.headers["Content-Security-Policy"]
                assert response.status == 200, path
            assert not re.search(r"https?://|//cdn|@import", body), path
            assert "default-src 'none'" in policy
            assert "connect-src 'self'" in policy
            assert "script-src 'self'" in policy and "style-src 'self'" in policy
            assert "unsafe-inline" not in policy and "unsafe-eval" not in policy

    async def test_only_the_three_page_files_are_served(self, served: Served) -> None:
        for path in ("/translation_page.py", "/index.html", "/%2e%2e/pyproject.toml", "/api"):
            async with served.session.get(path) as response:
                assert response.status == 404, path

    async def test_every_field_of_the_page_has_a_label(self, served: Served) -> None:
        async with served.session.get("/") as response:
            html = await response.text()

        collector = FieldsAndLabels()
        collector.feed(html)

        assert len(collector.fields) >= 4
        unlabelled = [f for f in collector.fields if f not in collector.labelled]
        assert unlabelled == []

    async def test_a_page_in_another_site_cannot_read_it_by_renaming_its_own_host(
        self, served: Served
    ) -> None:
        for path in ("/", "/api/info"):
            async with served.session.get(path, headers={"Host": "evil.example"}) as response:
                assert response.status == 403, path
        port = served.server.port
        async with served.session.get("/", headers={"Host": f"localhost:{port}"}) as response:
            assert response.status == 200

    async def test_a_post_from_another_site_is_refused_before_any_model_call(
        self, served: Served
    ) -> None:
        async with served.session.post(
            "/api/translate", json={"text": "hi"}, headers={"Origin": "http://evil.example"}
        ) as response:
            status = response.status
        async with served.session.post(
            "/api/translate", data='{"text": "hi"}', headers={"Content-Type": "text/plain"}
        ) as response:
            plain = response.status
        own = served.server.url
        ok, _ = await served.post("/api/translate", {"text": "hi"}, headers={"Origin": own})

        assert (status, plain, ok) == (403, 415, 200)
        assert len(served.fake.requests) == 1


class FieldsAndLabels(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: list[str] = []
        self.labelled: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "label" and values.get("for"):
            self.labelled.add(str(values["for"]))
        if tag in {"input", "select", "textarea"} and values.get("type") != "hidden":
            name = str(values.get("id") or "<no id>")
            self.fields.append(name)
            if values.get("aria-label") or values.get("aria-labelledby"):
                self.labelled.add(name)


class TestWhatIsAlwaysVisible:
    async def test_a_model_on_this_computer_is_named_with_its_endpoint_and_keeps_the_text_here(
        self, served: Served
    ) -> None:
        status, info = await served.get("/api/info")

        assert status == 200
        assert info["model"] == "test-model"
        assert info["endpoint"] == served.fake.base_url.removeprefix("http://")
        assert info["where"] == "stays on this computer" and info["local"] is True
        assert info["env_name"] == served.env_name
        assert info["read_language"] == "en"
        assert {"code": "ko", "name": "Korean"} in info["targets"]
        assert info["ratings"] == ["usable", "needs edits", "wrong"]

    async def test_a_hosted_model_says_where_the_text_is_sent_and_no_call_is_made_to_learn_it(
        self, fake: FakeTranslatorServer, tmp_path: Path
    ) -> None:
        env = write_env(tmp_path, "https://api.example.com/v1")
        server = page.PageServer(
            lab.load_profile(env), env_name=env.name, port=0, detect=False, results_dir=tmp_path
        )
        async with (
            server,
            aiohttp.ClientSession(base_url=server.url) as session,
            session.get("/api/info") as response,
        ):
            info = await response.json()

        assert info["where"] == "is sent to api.example.com" and info["local"] is False
        assert info["endpoint"] == "api.example.com/v1"
        assert fake.requests == []

    async def test_the_api_key_is_in_no_answer(self, served: Served) -> None:
        texts = []
        for path in ("/", "/app.js", "/api/info"):
            async with served.session.get(path) as response:
                texts.append(await response.text())
        _, translated = await served.translate("hello there")
        texts.append(str(translated))

        assert not any(SECRET_KEY in text for text in texts)


class TestOneMessage:
    async def test_it_shows_the_translation_the_time_and_a_clean_check(
        self, served: Served
    ) -> None:
        served.fake.enqueue(Answer("The blue box is on the table."))

        status, body = await served.translate(KOREAN)

        assert status == 200 and body["ok"] is True
        assert body["translation"] == "The blue box is on the table."
        assert body["target"] == "en" and body["seconds"] >= 0
        assert body["slack_tokens"] == {"ok": True, "summary": "ok"}
        assert body["detected"] is None
        [request] = served.fake.requests
        assert request.user_text == KOREAN
        assert "English" in request.system_message

    async def test_the_target_is_a_choice_so_english_to_korean_works_and_en_is_the_default(
        self, served: Served
    ) -> None:
        served.fake.enqueue(Answer("파란 상자는 탁자 위에 있어요."), Answer("fine"))

        _, korean = await served.translate(ENGLISH, "ko")
        _, default = await served.post("/api/translate", {"text": ENGLISH})

        assert korean["translation"] == "파란 상자는 탁자 위에 있어요."
        assert "Korean" in served.fake.requests[0].system_message
        assert default["target"] == "en"

    async def test_it_shows_the_language_and_the_confidence_when_the_tool_has_them(
        self, detecting: Served
    ) -> None:
        _, korean = await detecting.translate(KOREAN)
        _, english = await detecting.translate(ENGLISH, "ko")

        assert korean["detected"]["code"] == "ko" and korean["detected"]["name"] == "Korean"
        assert korean["detected"]["confidence"] == pytest.approx(1.0, abs=0.01)
        assert english["detected"]["code"] == "en"
        assert 0 < english["detected"]["confidence"] <= 1
        assert korean["decision"] and english["decision"]

    async def test_a_mention_link_emoji_or_code_that_is_missing_or_added_is_flagged(
        self, served: Served
    ) -> None:
        served.fake.enqueue(
            Answer("Hello there, look at this."), Answer(f"Hi {SLACKY} <!channel>"), Answer(SLACKY)
        )

        _, dropped = await served.translate(SLACKY)
        _, added = await served.translate(SLACKY)
        _, kept = await served.translate(SLACKY)

        assert dropped["slack_tokens"]["ok"] is False
        assert "<@U012ABCDE>" in dropped["slack_tokens"]["summary"]
        assert added["slack_tokens"]["ok"] is False
        assert "<!channel>" in added["slack_tokens"]["summary"]
        assert dropped["translation"] == "Hello there, look at this."
        assert kept["slack_tokens"]["ok"] is True

    @pytest.mark.parametrize("text", ["", "   \n\t", None])
    async def test_empty_input_is_refused_with_what_to_do_and_no_model_call(
        self, served: Served, text: str | None
    ) -> None:
        status, body = await served.post("/api/translate", {"text": text, "target": "en"})

        assert status == 422 and body["problem"]["kind"] == "empty"
        assert body["problem"]["advice"]
        assert served.fake.requests == []

    async def test_a_target_that_is_not_a_language_code_is_refused_before_any_call(
        self, served: Served
    ) -> None:
        status, body = await served.translate("hello", "en; drop table")

        assert status == 400 and body["problem"]["kind"] == "bad_target"
        assert served.fake.requests == []

    @pytest.mark.parametrize(
        ("behaviour", "kind"),
        [
            (Fail(404), "model_not_found"),
            (Fail(401), "key_refused"),
            (Fail(403), "key_refused"),
            (Fail(429), "rate_limited"),
            (Fail(500, echo_input=True), "server_error"),
            (Fail(400), "rejected"),
            (Malformed("empty_content"), "bad_answer"),
            (Malformed("not_json"), "bad_answer"),
        ],
    )
    async def test_each_failure_says_what_happened_and_what_to_do_and_never_echoes_the_provider(
        self, served: Served, behaviour: Fail | Malformed, kind: str
    ) -> None:
        served.fake.enqueue(behaviour)

        status, body = await served.translate("SOURCE-MARKER hello")

        assert status == 200 and body["ok"] is False
        problem = body["problem"]
        assert problem["kind"] == kind and problem["title"] and problem["advice"]
        everything = str(body)
        assert "SOURCE-MARKER" not in everything and "fake provider error" not in everything
        assert body["translation"] == "" and body["seconds"] >= 0

    async def test_a_model_that_does_not_answer_in_time_is_a_timeout_naming_the_setting(
        self, impatient: Served
    ) -> None:
        impatient.fake.enqueue(Hang())

        _, body = await impatient.translate("hello")

        assert body["ok"] is False and body["problem"]["kind"] == "timeout"
        assert "TRANSLATOR_TIMEOUT" in body["problem"]["advice"]
        assert impatient.env_name in body["problem"]["advice"]
        assert 0.9 <= body["seconds"] < 4

    async def test_an_endpoint_that_cannot_be_reached_names_the_endpoint_and_the_file(
        self, tmp_path: Path
    ) -> None:
        async with FakeTranslatorServer() as gone:
            dead_url = gone.base_url
        env = write_env(tmp_path, dead_url)
        server = page.PageServer(
            lab.load_profile(env), env_name=env.name, port=0, detect=False, results_dir=tmp_path
        )
        async with (
            server,
            aiohttp.ClientSession(base_url=server.url) as session,
            session.post("/api/translate", json={"text": "hello"}) as response,
        ):
            body = await response.json()

        problem = body["problem"]
        assert body["ok"] is False and problem["kind"] == "unreachable"
        assert dead_url.removeprefix("http://") in problem["advice"]
        assert env.name in problem["advice"]

    async def test_a_second_translation_is_refused_while_one_is_running(
        self, served: Served
    ) -> None:
        served.fake.set_default(Answer(delay=0.6))
        first = asyncio.create_task(served.translate("first"))
        await served.fake.wait_for_requests(1)

        status, body = await served.translate("second")
        first_status, first_body = await first

        assert status == 409 and body["problem"]["kind"] == "busy"
        assert first_status == 200 and first_body["ok"] is True
        assert len(served.fake.requests) == 1
        again_status, _ = await served.translate("third")
        assert again_status == 200


class TestAFileOfMessages:
    async def test_a_text_file_is_read_one_message_per_line_in_order(self, served: Served) -> None:
        status, started = await served.start_file(f"{KOREAN}\n\n{SLACKY}\n세 번째 메시지입니다\r\n")
        job = await served.finished_job()

        assert status == 202 and started["total"] == 3
        assert [r["source"] for r in job["rows"]] == [KOREAN, SLACKY, "세 번째 메시지입니다"]
        assert [r.user_text for r in served.fake.requests] == [r["source"] for r in job["rows"]]
        assert job["state"] == "done" and job["total"] == 3 and job["target"] == "en"
        assert [r["index"] for r in job["rows"]] == [0, 1, 2]

    async def test_a_csv_file_uses_the_message_column_like_the_terminal_tool(
        self, served: Served
    ) -> None:
        content = 'id,message,who\n1,"two\nlines",ana\n2,second one,bo\n3,,cy\n'

        status, started = await served.start_file(content, name="sample.csv")
        job = await served.finished_job()

        assert status == 202 and started["total"] == 2
        assert [r["source"] for r in job["rows"]] == ["two\nlines", "second one"]

    async def test_a_failed_message_is_a_row_and_the_run_goes_on(self, served: Served) -> None:
        served.fake.enqueue(Answer("one"), Fail(500), Answer("<@U012ABCDE> gone"))

        await served.start_file("first\nsecond\nthird <@U012ABCDE>\n")
        job = await served.finished_job()

        worked = [row["ok"] for row in job["rows"]]
        assert worked == [True, False, True] and job["state"] == "done"
        assert job["rows"][1]["problem"]["kind"] == "server_error"
        assert job["rows"][2]["slack_tokens"]["ok"] is True
        assert job["summary"]["translated"] == 2 and job["summary"]["failed"] == 1
        assert job["summary"]["flagged"] == 0

    async def test_a_row_that_lost_a_token_counts_as_flagged(self, served: Served) -> None:
        served.fake.enqueue(Answer("no tokens left"))

        await served.start_file("keep <@U012ABCDE> here\n")
        job = await served.finished_job()

        assert job["rows"][0]["slack_tokens"]["ok"] is False
        assert job["summary"]["flagged"] == 1

    async def test_rows_arrive_while_the_job_runs_and_a_poll_only_returns_the_new_ones(
        self, served: Served
    ) -> None:
        served.fake.set_default(Answer(delay=0.25))

        await served.start_file("a\nb\nc\n")
        seen: list[int] = []
        states: set[str] = set()
        while True:
            job = await served.job(after=len(seen))
            states.add(job["state"])
            seen.extend(row["index"] for row in job["rows"])
            if job["state"] != "running":
                break
            await asyncio.sleep(0.05)

        assert seen == [0, 1, 2]
        assert "running" in states and job["state"] == "done"

    async def test_only_one_job_runs_at_a_time(self, served: Served) -> None:
        served.fake.set_default(Answer(delay=0.5))
        await served.start_file("a\nb\n")
        await served.fake.wait_for_requests(1)

        status, body = await served.start_file("c\n")
        translate_status, translate_body = await served.translate("d")
        await served.finished_job()

        assert (status, translate_status) == (409, 409)
        assert body["problem"]["kind"] == "busy" and translate_body["problem"]["kind"] == "busy"
        assert len(served.fake.requests) == 2

    async def test_stop_ends_the_run_and_keeps_what_was_translated(self, served: Served) -> None:
        served.fake.set_default(Answer(delay=0.4))
        _, started = await served.start_file("a\nb\nc\nd\n")
        await served.first_row()

        status, stopped = await served.post("/api/job/stop", {"id": started["id"]})
        job = await served.job()

        assert status == 200 and stopped["state"] == "stopped"
        assert job["state"] == "stopped" and 1 <= len(job["rows"]) < 4
        after_status, _ = await served.translate("e")
        assert after_status == 200

    async def test_an_empty_or_oversized_file_is_a_clear_problem_before_any_call(
        self, served: Served
    ) -> None:
        empty_status, empty = await served.start_file("\n  \n", name="empty.txt")
        big_status, big = await served.start_file("x" * (page.MAX_UPLOAD_BYTES + 1))
        bad_status, bad = await served.start_file("hello\n", target="en/../ko")

        assert empty_status == 400 and empty["problem"]["kind"] == "bad_file"
        assert "no messages" in empty["problem"]["title"].lower()
        assert big_status == 413 and big["problem"]["kind"] == "too_large"
        assert bad_status == 400 and bad["problem"]["kind"] == "bad_target"
        assert served.fake.requests == []

    async def test_a_reloaded_page_gets_the_job_back_and_no_job_is_null(
        self, served: Served
    ) -> None:
        assert await served.job() is None

        await served.start_file("a\nb\n")
        await served.finished_job()
        job = await served.job()

        assert len(job["rows"]) == 2 and job["saved_to"] is None


class TestSavingTheMarks:
    async def finished(self, served: Served, content: str = "one\ntwo\nthree\n") -> dict[str, Any]:
        await served.start_file(content, name="my sample.txt")
        return await served.finished_job()

    async def test_the_rating_and_the_note_of_every_row_go_into_the_results_file(
        self, served: Served
    ) -> None:
        served.fake.set_default(Answer("TRANSLATION"))
        job = await self.finished(served)
        note = 'wrong, "quoted"\nsecond line 파란'
        marks = [
            {"rating": "usable", "note": ""},
            {"rating": "wrong", "note": note},
            {"rating": "", "note": "just a note"},
        ]

        status, saved = await served.post("/api/job/save", {"id": job["id"], "marks": marks})

        assert status == 200
        path = Path(saved["path"]) if Path(saved["path"]).is_absolute() else REPO / saved["path"]
        assert path.parent == served.results and path.name.startswith("my_sample-")
        rows = read_rows(path)
        assert [(r["rating"], r["notes"]) for r in rows] == [
            ("usable", ""),
            ("wrong", note),
            ("", "just a note"),
        ]
        assert [r["source"] for r in rows] == ["one", "two", "three"]
        assert {r["translation"] for r in rows} == {"TRANSLATION"}
        assert (rows[0]["model"], rows[0]["worked"]) == ("test-model", "yes")

    async def test_saving_again_replaces_the_same_file_and_the_job_remembers_where(
        self, served: Served
    ) -> None:
        job = await self.finished(served, "one\ntwo\n")

        _, first = await served.post(
            "/api/job/save", {"id": job["id"], "marks": [{"rating": "wrong", "note": "x"}]}
        )
        _, second = await served.post(
            "/api/job/save",
            {"id": job["id"], "marks": [{"rating": "usable", "note": "y"}] * 2},
        )

        assert first["path"] == second["path"]
        assert len(list(served.results.glob("*.csv"))) == 1
        assert [r["rating"] for r in read_rows(next(served.results.glob("*.csv")))] == [
            "usable",
            "usable",
        ]
        assert (await served.job())["saved_to"] == second["path"]

    async def test_a_save_that_is_not_valid_changes_nothing(self, served: Served) -> None:
        job = await self.finished(served, "one\ntwo\n")
        good = {"rating": "usable", "note": ""}

        results = [
            await served.post("/api/job/save", {"id": "nope", "marks": [good, good]}),
            await served.post("/api/job/save", {"id": job["id"], "marks": [good, good, good]}),
            await served.post(
                "/api/job/save", {"id": job["id"], "marks": [good, {"rating": "great", "note": ""}]}
            ),
            await served.post("/api/job/save", {"id": job["id"], "marks": [good, {"note": 5}]}),
            await served.post("/api/job/save", {"id": job["id"], "marks": "usable"}),
        ]

        assert [status for status, _ in results] == [404, 400, 400, 400, 400]
        assert all(body["problem"]["advice"] for _, body in results)
        assert not served.results.exists() or list(served.results.iterdir()) == []

    async def test_a_run_that_is_still_going_cannot_be_saved(self, served: Served) -> None:
        served.fake.set_default(Answer(delay=0.5))
        _, started = await served.start_file("one\ntwo\n")

        status, body = await served.post("/api/job/save", {"id": started["id"], "marks": []})
        await served.finished_job()

        assert status == 409 and body["problem"]["kind"] == "busy"

    async def test_the_browser_cannot_choose_where_the_file_goes(self, served: Served) -> None:
        await served.start_file("one\n", name="../../../evil.txt")
        job = await served.finished_job()

        _, saved = await served.post(
            "/api/job/save",
            {"id": job["id"], "marks": [{"rating": "usable", "note": ""}], "path": "/tmp/x.csv"},
        )

        [written] = list(served.results.iterdir())
        assert written.parent == served.results and written.suffix == ".csv"
        assert ".." not in written.name and "/" not in written.name
        assert saved["path"].endswith(written.name)


class TestNothingIsKeptOrLogged:
    async def test_nothing_is_stored_unless_the_person_saves(
        self, served: Served, tmp_path: Path
    ) -> None:
        before = files_under(tmp_path)

        await served.translate("hello there")
        await served.start_file("one\ntwo\n")
        await served.finished_job()

        assert files_under(tmp_path) == before
        assert not files_under(served.results)

    async def test_no_message_text_reaches_a_log_even_when_the_provider_echoes_it(
        self, served: Served, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        served.fake.enqueue(Answer("TRANSLATED-MARKER"), Fail(500, echo_input=True))

        await served.translate("SOURCE-MARKER one")
        await served.translate("SOURCE-MARKER two")
        await served.start_file("SOURCE-MARKER three\n")
        await served.finished_job()

        assert "SOURCE-MARKER" not in caplog.text and "TRANSLATED-MARKER" not in caplog.text
        assert "fake provider error" not in caplog.text

    async def test_the_only_calls_made_are_to_the_translator_one_per_message(
        self, served: Served
    ) -> None:
        await served.translate("one")
        await served.start_file("two\nthree\n")
        await served.finished_job()

        assert [r.path for r in served.fake.requests] == ["/chat/completions"] * 3
