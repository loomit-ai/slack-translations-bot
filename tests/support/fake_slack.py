"""TS-01 · A fake Slack: the Web API and response URLs on a local HTTP server.

It records every call in order and sorts each into read, private write or public write. A public
write fails the test that caused it. Scripted faults: an error answer, a rate limit, a delay,
an expired response URL. Made-up data only.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from aiohttp import web
from aiohttp.test_utils import TestServer

CallKind = Literal["read", "private_write", "public_write"]

READ_METHODS = frozenset(
    {
        "auth.test",
        "users.info",
        "conversations.history",
        "conversations.replies",
        "conversations.info",
        "conversations.members",
    }
)
PRIVATE_WRITE_METHODS = frozenset({"chat.postEphemeral", "views.open"})
RESPONSE_URL = "response_url"

HOME_TEAM = "T0HOME"
DEFAULT_SCOPES = (
    "commands,chat:write,channels:history,groups:history,channels:read,groups:read,"
    "users:read,users:read.email"
)


@dataclass
class RecordedCall:
    method: str
    kind: CallKind
    payload: dict[str, Any]


@dataclass
class _Fault:
    status: int = 200
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


class FakeSlackServer:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline
        self.calls: list[RecordedCall] = []
        self.users: dict[str, dict[str, Any]] = {}
        self.messages: dict[tuple[str, str], dict[str, Any]] = {}
        self.channels: dict[str, dict[str, Any]] = {}
        self.members: dict[str, list[str]] = {}
        self.team_id = HOME_TEAM
        self.scopes: str | None = DEFAULT_SCOPES
        self._faults: dict[str, list[_Fault]] = {}
        self._holds: dict[str, asyncio.Event] = {}
        self._server: TestServer | None = None

    async def start(self) -> None:
        app = web.Application()
        app.add_routes(
            [
                web.post("/api/{method}", self._api),
                web.get("/api/{method}", self._api),
                web.post("/response/{token}", self._response_url),
            ]
        )
        self._server = TestServer(app)
        await self._server.start_server()

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.close()

    @property
    def api_base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.port}/api/"

    def response_url(self, token: str = "one") -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.port}/response/{token}"

    def add_user(self, user_id: str, team_id: str | None = None, **fields: Any) -> None:
        self.users[user_id] = {
            "id": user_id,
            "team_id": team_id if team_id is not None else self.team_id,
            "is_bot": False,
            "deleted": False,
            "profile": {"email": "m1@x3mads.com"},
            **fields,
        }

    def add_message(
        self, channel: str, ts: str, text: str, user: str = "U0CUSTOMER", **fields: Any
    ) -> None:
        self.messages[(channel, ts)] = {
            "type": "message",
            "ts": ts,
            "text": text,
            "user": user,
            **fields,
        }

    def remove_message(self, channel: str, ts: str) -> None:
        self.messages.pop((channel, ts), None)

    def add_channel(self, channel: str, is_member: bool = True) -> None:
        self.channels[channel] = {"id": channel, "is_member": is_member}

    def fail_next(
        self, method: str, error: str, status: int = 200, retry_after: float | None = None
    ) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
        body = {"ok": False, "error": error}
        self._faults.setdefault(method, []).append(_Fault(status, body, headers))

    def drop_next(self, method: str) -> None:
        """Close the connection without answering the next call to `method`."""
        self._faults.setdefault(method, []).append(_Fault(status=-1))

    def delay_next(self, method: str, seconds: float) -> None:
        """Answer the next call to `method` normally, but only after `seconds`."""
        self._faults.setdefault(method, []).append(_Fault(delay=seconds))

    def hold(self, method: str) -> asyncio.Event:
        """Make `method` wait until the returned event is set."""
        event = asyncio.Event()
        self._holds[method] = event
        return event

    def calls_of(self, method: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.method == method]

    def calls_of_kind(self, kind: CallKind) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == kind]

    def assert_no_public_writes(self) -> None:
        public = [call.method for call in self.calls_of_kind("public_write")]
        assert not public, f"public Slack writes: {public}"

    async def _api(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = await _payload(request)
        kind: CallKind = (
            "read"
            if method in READ_METHODS
            else "private_write"
            if method in PRIVATE_WRITE_METHODS
            else "public_write"
        )
        self.calls.append(RecordedCall(method, kind, payload))
        if self.timeline is not None:
            self.timeline.append(f"slack:{method}")

        if method in self._holds:
            await self._holds[method].wait()
        fault = self._next_fault(method)
        if fault is not None and fault.delay:
            await asyncio.sleep(fault.delay)
            fault = None
        if fault is not None:
            if fault.status == -1 and request.transport is not None:
                request.transport.close()
            return web.json_response(
                fault.body, status=max(fault.status, 200), headers=fault.headers
            )
        headers = {}
        if method == "auth.test" and self.scopes is not None:
            headers["x-oauth-scopes"] = self.scopes
        return web.json_response(self._answer(method, payload), headers=headers)

    async def _response_url(self, request: web.Request) -> web.Response:
        payload = await _payload(request)
        private = (
            payload.get("response_type") == "ephemeral" or payload.get("delete_original") is True
        )
        self.calls.append(
            RecordedCall(RESPONSE_URL, "private_write" if private else "public_write", payload)
        )
        if self.timeline is not None:
            self.timeline.append(f"slack:{RESPONSE_URL}")
        if RESPONSE_URL in self._holds:
            await self._holds[RESPONSE_URL].wait()
        fault = self._next_fault(RESPONSE_URL)
        if fault is not None and fault.delay:
            await asyncio.sleep(fault.delay)
            fault = None
        if fault is not None:
            return web.Response(text=(fault.body or {}).get("error", "error"), status=fault.status)
        return web.Response(text="ok")

    def _next_fault(self, method: str) -> _Fault | None:
        queue = self._faults.get(method)
        return queue.pop(0) if queue else None

    def _answer(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if method == "auth.test":
            return {
                "ok": True,
                "url": "https://x3m.example.test/",
                "team": "X3M",
                "team_id": self.team_id,
                "user": "x3m_translator",
                "user_id": "U0BOT",
                "bot_id": "B0BOT",
            }
        if method == "users.info":
            user = self.users.get(str(payload.get("user")))
            return {"ok": True, "user": user} if user else {"ok": False, "error": "user_not_found"}
        if method == "conversations.info":
            channel = self.channels.get(str(payload.get("channel")))
            return (
                {"ok": True, "channel": channel}
                if channel
                else {"ok": False, "error": "channel_not_found"}
            )
        if method == "conversations.members":
            return {
                "ok": True,
                "members": self.members.get(str(payload.get("channel")), []),
                "response_metadata": {"next_cursor": ""},
            }
        if method in ("conversations.history", "conversations.replies"):
            return self._messages(method, payload)
        return {"ok": True}

    def _messages(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        channel = str(payload.get("channel"))
        oldest = str(payload.get("oldest"))
        found = [
            message
            for (message_channel, ts), message in self.messages.items()
            if message_channel == channel and ts == oldest
        ]
        if not found and method == "conversations.history":
            return {"ok": True, "messages": []}
        if method == "conversations.replies":
            root = self.messages.get((channel, str(payload.get("ts"))))
            if root is None:
                return {"ok": False, "error": "thread_not_found"}
            return {"ok": True, "messages": [root, *[m for m in found if m is not root]]}
        return {"ok": True, "messages": found}


async def _payload(request: web.Request) -> dict[str, Any]:
    if request.method == "GET":
        return dict(request.query)
    if request.content_type == "application/json":
        data = await request.json()
        return data if isinstance(data, dict) else {}
    form = await request.post()
    payload: dict[str, Any] = {}
    for key, value in form.items():
        text = str(value)
        if key in ("blocks", "view") and text[:1] in "[{":
            payload[key] = json.loads(text)
        else:
            payload[key] = text
    return payload
