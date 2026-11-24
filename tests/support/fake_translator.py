"""TS-02 · A fake translator endpoint: `POST /chat/completions` on a local port.

Tests start it in-process (`async with FakeTranslatorServer() as fake`) and script it with
`enqueue(...)`: each queued behaviour answers one request, then `default` answers the rest. It
records every request, so tests can count calls and check that the source text sat only in the
user message.

A person can also run it alone (`make fake-translator`) and point a development or staging bot
at it with `TRANSLATOR_BASE_URL`, to provoke timeouts, rate limits and bad answers without any
fault code inside the bot. In that mode it never prints request text.
"""

import argparse
import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self, cast, get_args

from aiohttp import web

OBEY_PAYLOAD = "<!channel> <https://injected.example|click here>"
DEFAULT_PORT = 8089
_DEFAULT_PREFIX = "[fake translation] "


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    raw_body: bytes

    @property
    def body(self) -> dict[str, Any]:
        parsed = json.loads(self.raw_body)
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}

    @property
    def messages(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.body.get("messages", []))

    def _content_of(self, role: str) -> str:
        return "\n".join(str(m.get("content", "")) for m in self.messages if m.get("role") == role)

    @property
    def system_message(self) -> str:
        return self._content_of("system")

    @property
    def user_text(self) -> str:
        return self._content_of("user")


Responder = Callable[[RecordedRequest], str]


def obey_injected_instructions(request: RecordedRequest) -> str:
    """What a gullible model answers when the text tells it to add a mention or a link."""
    return f"{request.user_text} {OBEY_PAYLOAD}"


@dataclass(frozen=True, slots=True)
class Answer:
    """A well-formed answer. `text=None` answers with the source text behind a fixed prefix."""

    text: str | Responder | None = None
    delay: float = 0.0
    finish_reason: str | None = "stop"
    prompt_tokens: int | None = 12
    completion_tokens: int | None = 7


@dataclass(frozen=True, slots=True)
class Fail:
    """An HTTP error. `echo_input` puts the source text and the system message in the error body,
    the way some providers do."""

    status: int
    retry_after: str | float | None = None
    delay: float = 0.0
    echo_input: bool = False


MalformedKind = Literal[
    "not_json",
    "json_array",
    "no_choices",
    "empty_choices",
    "empty_content",
    "null_content",
    "list_content",
    "truncated",
    "content_filter",
]


@dataclass(frozen=True, slots=True)
class Malformed:
    """A 200 answer the adapter must treat as unusable."""

    kind: MalformedKind


@dataclass(frozen=True, slots=True)
class Hang:
    """Never answers (until the server stops or the client gives up)."""


@dataclass(frozen=True, slots=True)
class Drop:
    """Closes the connection without answering."""


Behavior = Answer | Fail | Malformed | Hang | Drop


def _completion(content: Any, finish_reason: str | None = "stop", usage: bool = True) -> object:
    body: dict[str, object] = {
        "id": "fake-completion",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    return body


class FakeTranslatorServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        default: Behavior | None = None,
    ) -> None:
        self.requests: list[RecordedRequest] = []
        self._host = host
        self._port = port
        self._default: Behavior = default if default is not None else Answer()
        self._queue: deque[Behavior] = deque()
        self._runner: web.AppRunner | None = None
        self._arrived = asyncio.Event()

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def wait_for_requests(self, count: int = 1) -> None:
        while len(self.requests) < count:
            self._arrived.clear()
            await self._arrived.wait()

    def enqueue(self, *behaviors: Behavior) -> None:
        self._queue.extend(behaviors)

    def set_default(self, behavior: Behavior) -> None:
        self._default = behavior

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/chat/completions", self._handle)
        runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=0.1)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        self._runner = runner
        self._port = int(runner.addresses[0][1])

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

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

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        recorded = RecordedRequest(
            method=request.method,
            path=request.path,
            headers={key.lower(): value for key, value in request.headers.items()},
            raw_body=await request.read(),
        )
        self.requests.append(recorded)
        self._arrived.set()
        behavior = self._queue.popleft() if self._queue else self._default
        return await self._respond(request, recorded, behavior)

    async def _respond(
        self, request: web.Request, recorded: RecordedRequest, behavior: Behavior
    ) -> web.StreamResponse:
        match behavior:
            case Answer():
                await asyncio.sleep(behavior.delay)
                if behavior.text is None:
                    text = f"{_DEFAULT_PREFIX}{recorded.user_text}"
                elif callable(behavior.text):
                    text = behavior.text(recorded)
                else:
                    text = behavior.text
                with_usage = behavior.prompt_tokens is not None
                body = cast(dict[str, Any], _completion(text, behavior.finish_reason, with_usage))
                if with_usage:
                    body["usage"] = {
                        "prompt_tokens": behavior.prompt_tokens,
                        "completion_tokens": behavior.completion_tokens,
                    }
                return web.json_response(body)
            case Fail():
                await asyncio.sleep(behavior.delay)
                message = "fake provider error"
                if behavior.echo_input:
                    message += f": {recorded.system_message} | {recorded.user_text}"
                headers = {}
                if behavior.retry_after is not None:
                    headers["Retry-After"] = str(behavior.retry_after)
                return web.json_response(
                    {"error": {"message": message, "type": "fake_error", "code": behavior.status}},
                    status=behavior.status,
                    headers=headers,
                )
            case Malformed():
                return _malformed(behavior.kind)
            case Hang():
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
            case Drop():
                if request.transport is not None:
                    request.transport.abort()
                return web.Response(status=500)


def _malformed(kind: MalformedKind) -> web.StreamResponse:
    match kind:
        case "not_json":
            return web.Response(text="this is not json", content_type="text/plain")
        case "json_array":
            return web.json_response([])
        case "no_choices":
            return web.json_response({"id": "fake-completion"})
        case "empty_choices":
            return web.json_response({"choices": []})
        case "empty_content":
            return web.json_response(_completion("   \n"))
        case "null_content":
            return web.json_response(_completion(None))
        case "list_content":
            return web.json_response(_completion([{"type": "text", "text": "hola"}]))
        case "truncated":
            return web.json_response(_completion("a translation that stops mid", "length"))
        case "content_filter":
            return web.json_response(_completion("blocked", "content_filter"))


def behavior_from_arguments(arguments: argparse.Namespace) -> tuple[list[Behavior], Behavior]:
    """The queue and the default that the command-line switches describe."""
    healthy = Answer(
        delay=arguments.delay, text=obey_injected_instructions if arguments.obey else None
    )
    if arguments.hang:
        return [], Hang()
    if arguments.malformed:
        return [], Malformed(arguments.malformed)
    if arguments.fail_status is None:
        return [], healthy
    failure = Fail(
        arguments.fail_status,
        retry_after=arguments.retry_after,
        delay=arguments.delay,
        echo_input=arguments.echo_error,
    )
    if arguments.fail_first is None:
        return [], failure
    return [failure] * arguments.fail_first, healthy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.support.fake_translator",
        description="Fake translator endpoint for manual fault tests (TS-02).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds before each answer")
    parser.add_argument("--fail-status", type=int, help="answer with this HTTP status instead")
    parser.add_argument("--fail-first", type=int, help="fail only this many requests, then answer")
    parser.add_argument("--retry-after", help="Retry-After header on the failures")
    parser.add_argument("--echo-error", action="store_true", help="echo the input in error bodies")
    parser.add_argument(
        "--malformed", choices=get_args(MalformedKind), help="answer 200 but unusable"
    )
    parser.add_argument("--hang", action="store_true", help="never answer")
    parser.add_argument("--obey", action="store_true", help="obey instructions found in the text")
    return parser


async def _serve(server: FakeTranslatorServer) -> None:
    await server.start()
    print(f"fake translator ready: TRANSLATOR_BASE_URL={server.base_url}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    queue, default = behavior_from_arguments(arguments)
    server = FakeTranslatorServer(host=arguments.host, port=arguments.port, default=default)
    server.enqueue(*queue)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(server))


if __name__ == "__main__":
    main()
