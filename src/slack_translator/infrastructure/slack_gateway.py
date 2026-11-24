# pyright: basic
"""IF-05 · The Slack side of the ports: private output, message reads and member lookups.

The Slack SDK returns untyped data, so this boundary adapter is checked in basic mode.

Every outbound call has a deadline (REQ-10) and is counted (MET-07). Nothing here can post to a
channel with the bot token (ADR-006): the only writes are private.
"""

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import httpx
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from slack_translator.domain.errors import ResponseUrlUnusable, SlackDeliveryError
from slack_translator.domain.messages import (
    MessageRef,
    PrivateMessage,
    ResponseUrl,
    SourceMessage,
    UserFacts,
)
from slack_translator.domain.ports import Clock, SlackCallOutcome, Telemetry
from slack_translator.infrastructure.ttl_cache import TtlCache

RESPONSE_URL_MAX_USES = 5
RESPONSE_URL_LIFETIME = 30 * 60
LOOKUP_LIFETIME = 10 * 60
RETRY_AFTER_LIMIT = 5.0
_GONE = frozenset({"message_not_found", "thread_not_found", "channel_not_found"})


class SlackGateway:
    def __init__(
        self,
        client: AsyncWebClient,
        telemetry: Telemetry,
        clock: Clock,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self._telemetry = telemetry
        self._clock = clock
        self._url_use: dict[str, tuple[float, int]] = {}
        self._users: TtlCache[str, UserFacts] = TtlCache(clock, LOOKUP_LIFETIME, 5000)
        self._members: TtlCache[str, Sequence[str]] = TtlCache(clock, LOOKUP_LIFETIME, 200)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def via_response_url(
        self,
        url: ResponseUrl,
        message: PrivateMessage,
        mode: Literal["new", "replace", "delete"],
        thread_ts: str | None = None,
    ) -> None:
        key = hashlib.sha256(url.reveal().encode()).hexdigest()
        self._check_response_url(key)

        payload: dict[str, Any]
        if mode == "delete":
            payload = {"delete_original": True}
        else:
            payload = {
                "response_type": "ephemeral",
                "replace_original": mode == "replace",
                "text": message.text,
                "blocks": list(message.blocks),
            }
            if thread_ts is not None and mode == "new":
                payload["thread_ts"] = thread_ts

        try:
            response = await self._http.post(url.reveal(), json=payload)
        except httpx.TimeoutException:
            self._count("response_url", "timeout")
            raise ResponseUrlUnusable from None
        except httpx.HTTPError:
            self._count("response_url", "error")
            raise ResponseUrlUnusable from None

        self._note_use(key)
        if response.status_code == 429:
            self._count("response_url", "rate_limited")
            raise ResponseUrlUnusable
        if not response.is_success:
            self._count("response_url", "error", slack_error=f"http_{response.status_code}")
            raise ResponseUrlUnusable
        self._count("response_url", "ok")

    async def ephemeral(
        self,
        channel: str,
        user: str,
        message: PrivateMessage,
        thread_ts: str | None = None,
    ) -> None:
        arguments: dict[str, Any] = {
            "channel": channel,
            "user": user,
            "text": message.text,
            "blocks": list(message.blocks),
        }
        if thread_ts is not None:
            arguments["thread_ts"] = thread_ts
        try:
            await self._call(
                "chat.postEphemeral", lambda: self._client.chat_postEphemeral(**arguments)
            )
        except SlackApiError as error:
            raise SlackDeliveryError(_slack_error(error)) from None

    async def open_modal(self, trigger_id: str, view: dict[str, object]) -> None:
        try:
            await self._call(
                "views.open", lambda: self._client.views_open(trigger_id=trigger_id, view=view)
            )
        except SlackApiError as error:
            raise SlackDeliveryError(_slack_error(error)) from None

    async def fetch(self, ref: MessageRef) -> SourceMessage | None:
        try:
            if ref.thread_root_ts is not None:
                root = ref.thread_root_ts
                response = await self._call(
                    "conversations.replies",
                    lambda: self._client.conversations_replies(
                        channel=ref.channel,
                        ts=root,
                        oldest=ref.ts,
                        latest=ref.ts,
                        inclusive=True,
                        limit=5,
                    ),
                )
            else:
                response = await self._call(
                    "conversations.history",
                    lambda: self._client.conversations_history(
                        channel=ref.channel,
                        oldest=ref.ts,
                        latest=ref.ts,
                        inclusive=True,
                        limit=1,
                    ),
                )
        except SlackApiError as error:
            if _slack_error(error) in _GONE:
                return None
            raise SlackDeliveryError(_slack_error(error)) from None

        for item in response.get("messages") or []:
            if item.get("ts") == ref.ts and item.get("subtype") != "tombstone":
                return SourceMessage(ref=ref, text=item.get("text") or "", user_id=item.get("user"))
        return None

    async def user_facts(self, user_id: str) -> UserFacts | None:
        cached = self._users.get(user_id)
        if cached is not None:
            return cached
        try:
            response = await self._call("users.info", lambda: self._client.users_info(user=user_id))
        except Exception:
            # A lookup that fails for any reason means the workspace cannot be established,
            # and the caller refuses (REQ-05).
            return None
        user = response.get("user") or {}
        team_id = user.get("team_id")
        facts = UserFacts(
            team_id=team_id if isinstance(team_id, str) and team_id.strip() else None,
            is_bot=bool(user.get("is_bot")),
            deleted=bool(user.get("deleted")),
            is_stranger=bool(user.get("is_stranger")),
            # Only the domain is kept. The address goes no further than this line (CR-09).
            email_domain=email_domain_of((user.get("profile") or {}).get("email")),
        )
        if facts.team_id is not None:
            # An answer without a workspace is incomplete: it must not stick for 10 minutes.
            self._users.set(user_id, facts)
        return facts

    async def channel_members(self, channel_id: str) -> Sequence[str]:
        cached = self._members.get(channel_id)
        if cached is not None:
            return cached
        members: list[str] = []
        cursor: str | None = None
        while True:
            try:
                response = await self._members_page(channel_id, cursor)
            except SlackApiError as error:
                raise SlackDeliveryError(_slack_error(error)) from None
            members.extend(response.get("members") or [])
            cursor = (response.get("response_metadata") or {}).get("next_cursor") or None
            if cursor is None:
                break
        self._members.set(channel_id, tuple(members))
        return tuple(members)

    async def _members_page(self, channel_id: str, cursor: str | None) -> Any:
        return await self._call(
            "conversations.members",
            lambda: self._client.conversations_members(
                channel=channel_id, cursor=cursor, limit=200
            ),
        )

    async def _call(self, method: str, make_call: Callable[[], Awaitable[Any]]) -> Any:
        """One Slack call with a deadline, counted. One retry when Slack asks for a short wait."""
        for attempt in (1, 2):
            try:
                result = await make_call()
            except SlackApiError as error:
                status = getattr(error.response, "status_code", None)
                if status == 429:
                    wait = _retry_after(error)
                    if attempt == 1 and wait is not None and wait <= RETRY_AFTER_LIMIT:
                        self._count(method, "rate_limited")
                        await self._clock.sleep(wait)
                        continue
                    self._count(method, "rate_limited", slack_error="ratelimited")
                else:
                    self._count(method, "error", slack_error=_slack_error(error))
                raise
            except TimeoutError:
                self._count(method, "timeout")
                raise SlackApiError("timeout", {"error": "timeout"}) from None
            self._count(method, "ok")
            return result
        raise AssertionError("unreachable")

    def _count(
        self, method: str, outcome: SlackCallOutcome, slack_error: str | None = None
    ) -> None:
        self._telemetry.slack_call(method=method, outcome=outcome, slack_error=slack_error)

    def _check_response_url(self, key: str) -> None:
        entry = self._url_use.get(key)
        if entry is None:
            return
        first_seen, uses = entry
        if uses >= RESPONSE_URL_MAX_USES or self._clock.now() - first_seen >= RESPONSE_URL_LIFETIME:
            raise ResponseUrlUnusable

    def _note_use(self, key: str) -> None:
        first_seen, uses = self._url_use.get(key, (self._clock.now(), 0))
        if len(self._url_use) > 2000:
            self._url_use.pop(next(iter(self._url_use)))
        self._url_use[key] = (first_seen, uses + 1)


def email_domain_of(address: object) -> str | None:
    """The lower-cased domain of an address with exactly one usable `@`, else `None`.

    It never raises and never echoes its input, so an address cannot end up in an error.
    """
    if not isinstance(address, str):
        return None
    text = address.strip()
    if not text or any(char.isspace() for char in text):
        return None
    local, separator, domain = text.partition("@")
    if not separator or not local or not domain or "@" in domain:
        return None
    return domain.lower()


def _slack_error(error: SlackApiError) -> str:
    data = getattr(error.response, "data", None)
    if isinstance(data, dict):
        code = data.get("error")
        if isinstance(code, str):
            return code
    return "unknown"


def _retry_after(error: SlackApiError) -> float | None:
    headers = getattr(error.response, "headers", None) or {}
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
