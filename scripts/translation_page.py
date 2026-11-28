"""US-13 · A simple page on this computer to try translations outside Slack.

    make translation-page ENV=lab/local.env
    uv run python scripts/translation_page.py --env lab/local.env

It opens on http://127.0.0.1:8765 and listens on that address and nowhere else, so only this
computer can reach it and there is no sign-in. It uses the same functions as the two terminal tools
(`translation_lab.py`), so the bot's own translator is called unchanged, with the model named in
the env file. The page offers one message, or a file of messages to read and mark one by one.

Nothing leaves this computer except the call to the translator the env file names. No message text
is logged or kept: the only file written is the results file the person saves, in a folder git
ignores. The bot and its container never start this page.
"""

import argparse
import asyncio
import errno
import ipaddress
import json
import re
import secrets
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal, Self
from urllib.parse import urlsplit

from aiohttp import web

from slack_translator.domain.languages import SUPPORTED_LANGUAGES
from translation_lab import (
    RATINGS,
    REPO,
    Lab,
    Outcome,
    Profile,
    ProfileError,
    default_results_path,
    ensure_private_path,
    load_profile,
    parse_messages,
    run_all,
    summarize,
    use_utf8_output,
    validate_target,
    write_results,
)

HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
MAX_UPLOAD_BYTES: Final = 2 * 1024 * 1024
MAX_NOTE_CHARS: Final = 5000
ASSETS: Final = Path(__file__).resolve().parent / "translation_page_assets"
DEFAULT_RESULTS_DIR: Final = REPO / "lab" / "results"

_POLICY: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_SCRIPT_VARIANTS: Final = {
    "zh-Hans": "Chinese (Simplified)",
    "zh-Hant": "Chinese (Traditional)",
}


def is_this_computer(host: str) -> bool:
    name = host.lower().strip("[]")
    if name == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def where_text_goes(host: str) -> str:
    return "stays on this computer" if is_this_computer(host) else f"is sent to {host}"


def endpoint_label(base_url: str) -> str:
    """Host, port and path of the endpoint: never a password, query or fragment."""
    parts = urlsplit(base_url)
    host = parts.hostname or "unknown"
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    return f"{host}{port}{parts.path}"


def target_options() -> list[dict[str, str]]:
    names = {**SUPPORTED_LANGUAGES, **_SCRIPT_VARIANTS}
    ordered = sorted(names.items(), key=lambda item: (item[1], item[0]))
    return [{"code": code, "name": name} for code, name in ordered]


@dataclass(frozen=True, slots=True)
class Context:
    model: str
    env_name: str
    endpoint: str
    timeout: float


@dataclass(frozen=True, slots=True)
class Problem:
    """What happened, then what to do next. Never carries a message or the provider's answer."""

    kind: str
    title: str
    advice: str

    def as_json(self) -> dict[str, str]:
        return {"kind": self.kind, "title": self.title, "advice": self.advice}


def explain_failure(category: str, http_status: int | None, context: Context) -> Problem:
    """The words for a failed translation, from the error kind the translator reports."""
    env = context.env_name
    if category == "timeout":
        return Problem(
            "timeout",
            "The model took too long to answer",
            f"There was no answer within {context.timeout:g} seconds. The first call to a local "
            f"model can be slow while it loads, so try again. If it keeps happening, raise "
            f"TRANSLATOR_TIMEOUT in {env} (1 to 60) or use a smaller model.",
        )
    if category == "rate_limited":
        return Problem(
            "rate_limited",
            "The provider is limiting requests",
            "Wait a moment and try again. Free models and busy providers answer this way.",
        )
    if category == "invalid_response":
        return Problem(
            "bad_answer",
            "The model gave no usable answer",
            "The answer was empty or cut off. Try again, or try a model that does not print "
            "its reasoning.",
        )
    if category == "unavailable" and http_status is None:
        return Problem(
            "unreachable",
            "Could not reach the model server",
            f"The page could not connect to {context.endpoint}. Check that the model server is "
            f"running and that TRANSLATOR_BASE_URL in {env} is right, then try again. For a "
            f"local model, start Ollama or LM Studio and load {context.model}.",
        )
    if category == "unavailable":
        return Problem(
            "server_error",
            f"The model server reported an error (HTTP {http_status})",
            "A local server often answers this when it could not load the model, for example "
            "when memory is not enough: read the server's own log, then try again. A hosted "
            "provider may be having trouble, so try again later.",
        )
    if category == "rejected" and http_status == 404:
        return Problem(
            "model_not_found",
            f"The server does not know the model {context.model}",
            f"Check TRANSLATOR_MODEL in {env} against the names the server has (for Ollama, run "
            f"ollama list). A wrong TRANSLATOR_BASE_URL can give the same answer.",
        )
    if category == "rejected" and http_status in (401, 403):
        return Problem(
            "key_refused",
            "The server refused the key",
            f"Check TRANSLATOR_API_KEY in {env}. A local server takes any value; a hosted one "
            f"needs a valid key.",
        )
    if category == "rejected":
        return Problem(
            "rejected",
            f"The server refused the request (HTTP {http_status})",
            f"Check TRANSLATOR_MODEL and TRANSLATOR_EXTRA_BODY in {env}: the server did not "
            f"accept the request the bot makes.",
        )
    return Problem(
        "failed",
        "The translation failed",
        f"Try again. If it keeps failing, check the settings in {env} and the model server.",
    )


def empty_input() -> Problem:
    return Problem(
        "empty",
        "There is no message to translate",
        "Type or paste a message, then press Translate.",
    )


def bad_target(detail: str) -> Problem:
    return Problem("bad_target", "That is not a language code", detail)


def busy() -> Problem:
    return Problem(
        "busy",
        "Another translation is still running",
        "Wait for it to finish, or press Stop, then try again.",
    )


def bad_file() -> Problem:
    return Problem(
        "bad_file",
        "The file has no messages",
        "Use a .txt file with one message per line, or a .csv file with a message, text or "
        "source column (otherwise the first column is read).",
    )


def too_large() -> Problem:
    return Problem(
        "too_large",
        "That file is too large",
        f"Keep a file under {MAX_UPLOAD_BYTES // 2 // (1024 * 1024)} MB: split it into smaller "
        "files and translate them one after the other.",
    )


def bad_request() -> Problem:
    return Problem(
        "bad_request",
        "The page sent something the server could not read",
        "Reload the page and try again.",
    )


def unknown_job() -> Problem:
    return Problem(
        "unknown_job",
        "That run is no longer there",
        "The page was restarted or another run started. Translate the file again.",
    )


def bad_marks() -> Problem:
    return Problem(
        "bad_marks",
        "The ratings were not understood",
        "Use usable, needs edits or wrong for a rating, and text for a note.",
    )


class Refusal(Exception):
    def __init__(self, status: int, problem: Problem) -> None:
        super().__init__(problem.kind)
        self.status = status
        self.problem = problem


@dataclass(slots=True)
class Job:
    id: str
    sample: str
    target: str
    total: int
    results_path: Path
    outcomes: list[Outcome] = field(default_factory=list)
    state: Literal["running", "done", "stopped", "failed"] = "running"
    saved_to: str | None = None
    task: asyncio.Task[None] | None = None


def _clean_sample(name: object) -> tuple[str, bool]:
    """A safe file name for a results file and whether the sample is a CSV. The browser's file
    name is only ever used to word the results file name, never as a path."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(name, str) else ""
    path = Path(base)
    stem = re.sub(r"[^\w.-]+", "_", path.stem).strip("._")[:60] or "sample"
    is_csv = path.suffix.lower() == ".csv"
    return f"{stem}{'.csv' if is_csv else '.txt'}", is_csv


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(REPO)) if resolved.is_relative_to(REPO) else str(path)


class PageServer:
    """The page and the calls it makes, on 127.0.0.1 only. `async with PageServer(...)`."""

    def __init__(
        self,
        profile: Profile,
        *,
        env_name: str,
        port: int = DEFAULT_PORT,
        detect: bool = True,
        results_dir: Path = DEFAULT_RESULTS_DIR,
    ) -> None:
        ensure_private_path(results_dir / "results.csv")
        self._profile = profile
        self._env_name = env_name
        self._requested_port = port
        self._detect = detect
        self._results_dir = results_dir
        self._lab = Lab(profile, detect=detect)
        self._context = Context(
            model=profile.model,
            env_name=env_name,
            endpoint=endpoint_label(profile.settings.translator_base_url),
            timeout=profile.settings.translator_timeout,
        )
        self._assets = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/app.css": ("app.css", "text/css"),
        }
        self._bodies = {
            path: (ASSETS / name).read_bytes() for path, (name, _) in self._assets.items()
        }
        self._busy = False
        self._job: Job | None = None
        self._runner: web.AppRunner | None = None

    @property
    def context(self) -> Context:
        return self._context

    @property
    def addresses(self) -> list[Any]:
        return list(self._runner.addresses) if self._runner is not None else []

    @property
    def port(self) -> int:
        return int(self.addresses[0][1])

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    async def start(self) -> None:
        await self._lab.start()
        try:
            app = web.Application(client_max_size=MAX_UPLOAD_BYTES, middlewares=[self._guard])
            for path, (_, content_type) in self._assets.items():
                app.router.add_get(path, self._asset(path, content_type))
            app.router.add_get("/favicon.ico", self._no_content)
            app.router.add_get("/api/info", self._info)
            app.router.add_post("/api/translate", self._translate)
            app.router.add_post("/api/file", self._start_file)
            app.router.add_get("/api/job", self._read_job)
            app.router.add_post("/api/job/stop", self._stop_job)
            app.router.add_post("/api/job/save", self._save_job)
            self._runner = web.AppRunner(
                app, access_log=None, handler_cancellation=True, shutdown_timeout=1.0
            )
            await self._runner.setup()
            await web.TCPSite(self._runner, HOST, self._requested_port).start()
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        job = self._job
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        await self._lab.aclose()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()

    @web.middleware
    async def _guard(self, request: web.Request, handler: Any) -> web.StreamResponse:
        """Only this computer's own page may talk to the server: a web page in another tab could
        otherwise reach 127.0.0.1 too (a changed host name, or a post from another site)."""
        port = self.port
        hosts = {f"{HOST}:{port}", f"localhost:{port}"}
        if request.headers.get("Host", "") not in hosts:
            return web.Response(status=403, text=f"This page answers only on {HOST}.\n")
        origin = request.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in hosts}:
            return web.Response(status=403, text="This page does not answer other sites.\n")
        if request.method == "POST" and request.content_type != "application/json":
            return web.Response(status=415, text="The page sends JSON.\n")
        try:
            response = await handler(request)
        except Refusal as refusal:
            response = _problem(refusal.status, refusal.problem)
        response.headers["Content-Security-Policy"] = _POLICY
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def _asset(self, path: str, content_type: str) -> Any:
        body = self._bodies[path]

        async def serve(_: web.Request) -> web.Response:
            return web.Response(body=body, content_type=content_type, charset="utf-8")

        return serve

    async def _no_content(self, _: web.Request) -> web.Response:
        return web.Response(status=204)

    async def _info(self, _: web.Request) -> web.Response:
        settings = self._profile.settings
        return web.json_response(
            {
                "model": self._profile.model,
                "endpoint": self._context.endpoint,
                "host": self._profile.host,
                "where": where_text_goes(self._profile.host),
                "local": is_this_computer(self._profile.host),
                "env_name": self._env_name,
                "read_language": settings.read_language,
                "targets": target_options(),
                "ratings": list(RATINGS),
                "timeout": settings.translator_timeout,
                "max_file_bytes": MAX_UPLOAD_BYTES // 2,
                "detect": self._detect,
            }
        )

    async def _translate(self, request: web.Request) -> web.Response:
        body = await _read_json(request)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise Refusal(422, empty_input())
        target = self._target(body.get("target"))
        self._claim()
        try:
            outcome = await self._lab.run(text, target)
        finally:
            self._busy = False
        return web.json_response(self._view(outcome))

    async def _start_file(self, request: web.Request) -> web.Response:
        body = await _read_json(request)
        content = body.get("content")
        if not isinstance(content, str):
            raise Refusal(400, bad_request())
        target = self._target(body.get("target"))
        sample, is_csv = _clean_sample(body.get("name"))
        try:
            messages = parse_messages(content.removeprefix("﻿"), is_csv=is_csv, name=sample)
        except ProfileError:
            raise Refusal(400, bad_file()) from None
        self._claim()
        saved = default_results_path(Path(sample), self._profile, target, datetime.now())
        job = Job(
            id=secrets.token_hex(4),
            sample=sample,
            target=target,
            total=len(messages),
            results_path=self._results_dir / saved.name,
        )
        job.task = asyncio.create_task(self._run_job(job, messages))
        self._job = job
        return web.json_response({"id": job.id, "total": job.total}, status=202)

    async def _run_job(self, job: Job, messages: list[str]) -> None:
        try:
            await run_all(
                self._lab,
                messages,
                job.target,
                lambda _number, _total, outcome: job.outcomes.append(outcome),
            )
            job.state = "done"
        except asyncio.CancelledError:
            job.state = "stopped"
            raise
        except Exception as error:
            job.state = "failed"
            sys.stderr.write(f"The run stopped unexpectedly: {type(error).__name__}\n")
        finally:
            self._busy = False

    async def _read_job(self, request: web.Request) -> web.Response:
        job = self._job
        if job is None:
            return web.json_response({"job": None})
        try:
            after = max(int(request.query.get("after", "0")), 0)
        except ValueError:
            after = 0
        totals = summarize(job.outcomes)
        return web.json_response(
            {
                "job": {
                    "id": job.id,
                    "state": job.state,
                    "sample": job.sample,
                    "target": job.target,
                    "total": job.total,
                    "rows": [
                        self._view(outcome, index=index)
                        for index, outcome in enumerate(job.outcomes)
                        if index >= after
                    ],
                    "summary": {
                        "done": totals.total,
                        "translated": totals.translated,
                        "failed": totals.failed,
                        "flagged": totals.flagged,
                        "average_seconds": round(totals.average_seconds, 2),
                    },
                    "saved_to": job.saved_to,
                }
            }
        )

    async def _stop_job(self, request: web.Request) -> web.Response:
        job = self._known_job(await _read_json(request))
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        if job.state == "running":
            job.state = "stopped"
        self._busy = False
        return web.json_response({"state": job.state})

    async def _save_job(self, request: web.Request) -> web.Response:
        body = await _read_json(request)
        job = self._known_job(body)
        if job.state == "running":
            raise Refusal(409, busy())
        marks = body.get("marks")
        if not isinstance(marks, list) or len(marks) > len(job.outcomes):
            raise Refusal(400, bad_marks())
        marked = list(job.outcomes)
        for index, mark in enumerate(marks):
            rating, note = _read_mark(mark)
            marked[index] = replace(marked[index], rating=rating, notes=note)
        write_results(job.results_path, marked, self._profile)
        job.saved_to = _display_path(job.results_path)
        return web.json_response({"path": job.saved_to})

    def _known_job(self, body: dict[str, Any]) -> Job:
        job = self._job
        if job is None or body.get("id") != job.id:
            raise Refusal(404, unknown_job())
        return job

    def _target(self, value: object) -> str:
        if value is None or value == "":
            return self._profile.settings.read_language
        if not isinstance(value, str):
            raise Refusal(400, bad_target("Choose a language from the list."))
        try:
            return validate_target(value)
        except ProfileError as problem:
            raise Refusal(400, bad_target(str(problem))) from None

    def _claim(self) -> None:
        if self._busy:
            raise Refusal(409, busy())
        self._busy = True

    def _view(self, outcome: Outcome, *, index: int | None = None) -> dict[str, Any]:
        view: dict[str, Any] = {
            "ok": outcome.worked,
            "translation": outcome.translation,
            "target": outcome.target,
            "seconds": round(outcome.seconds, 2),
            "http_status": outcome.http_status,
            "slack_tokens": (
                {"ok": outcome.check.ok, "summary": outcome.check.describe()}
                if outcome.worked
                else None
            ),
            "detected": (
                {
                    "code": outcome.detected_code,
                    "name": outcome.detected_name,
                    "confidence": round(outcome.lead, 3),
                }
                if outcome.detected_code is not None and outcome.lead is not None
                else None
            ),
            "decision": outcome.decision,
            "tokens": (
                {"prompt": outcome.prompt_tokens, "completion": outcome.completion_tokens}
                if outcome.prompt_tokens is not None
                else None
            ),
            "problem": (
                None
                if outcome.worked
                else explain_failure(outcome.error, outcome.http_status, self._context).as_json()
            ),
        }
        if index is not None:
            view["index"] = index
            view["source"] = outcome.source
        return view


def _problem(status: int, problem: Problem) -> web.Response:
    return web.json_response({"problem": problem.as_json()}, status=status)


async def _read_json(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except web.HTTPRequestEntityTooLarge:
        raise Refusal(413, too_large()) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Refusal(400, bad_request()) from None
    if not isinstance(body, dict):
        raise Refusal(400, bad_request())
    return body


def _read_mark(mark: object) -> tuple[str, str]:
    if not isinstance(mark, dict):
        raise Refusal(400, bad_marks())
    rating = mark.get("rating", "")
    note = mark.get("note", "")
    if (
        not isinstance(rating, str)
        or rating not in ("", *RATINGS)
        or not isinstance(note, str)
        or len(note) > MAX_NOTE_CHARS
    ):
        raise Refusal(400, bad_marks())
    return rating, note


def _port(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = -1
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("a port is a number from 0 to 65535")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translation_page.py",
        description="A page on this computer to try translations outside Slack (US-13).",
    )
    parser.add_argument("--env", required=True, type=Path, help="env file with the model settings")
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT, help="default: %(default)s")
    parser.add_argument("--host", default=HOST, help=f"only {HOST} is allowed")
    parser.add_argument("--no-detect", action="store_true", help="skip Lingua")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="where the saved results go (default: lab/results; git must ignore it)",
    )
    return parser


async def run(arguments: argparse.Namespace) -> int:
    if arguments.host != HOST:
        raise ProfileError(
            f"this page listens only on {HOST}, which is this computer, so nothing else can "
            "reach it; there is no other address to choose"
        )
    profile = load_profile(arguments.env)
    server = PageServer(
        profile,
        env_name=arguments.env.name,
        port=arguments.port,
        detect=not arguments.no_detect,
        results_dir=arguments.results_dir.resolve(),
    )
    try:
        async with server:
            sys.stdout.write(
                f"Model: {profile.model} · Endpoint: {server.context.endpoint} · "
                f"Text {where_text_goes(profile.host)}\n"
                f"Open {server.url} in your browser. Stop with Ctrl-C.\n"
            )
            sys.stdout.flush()
            await asyncio.Event().wait()
    except OSError as problem:
        if problem.errno != errno.EADDRINUSE:
            raise ProfileError(
                f"could not listen on port {arguments.port}: {problem.strerror}"
            ) from None
        raise ProfileError(
            f"port {arguments.port} is already in use. Start the page on another port, for "
            f"example: make translation-page ENV={arguments.env} PORT={arguments.port + 1}"
        ) from None
    return 0


def main(argv: list[str]) -> int:
    use_utf8_output()
    arguments = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(arguments))
    except ProfileError as problem:
        sys.stderr.write(f"{problem}\n")
        return 2
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped.\n")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
