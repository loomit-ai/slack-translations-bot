# pyright: basic
"""ADR-002, ADR-003 · `create_app(settings)` builds everything; nothing happens at import time.

Start order (REQ-11): settings, models, `auth.test`, a membership check of each allowlisted
channel, then connect. `/readyz` turns ready only after all of it. On SIGTERM the bot stops
receiving, gives in-flight work 10 seconds, and exits 0.
"""

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aiohttp import web
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from slack_translator.actions.translate_message import (
    HomeWorkspace,
    TranslateMessage,
    TranslationPolicy,
)
from slack_translator.delivery import adapters
from slack_translator.delivery.background import TaskRegistry
from slack_translator.delivery.http.health import Readiness, build_health_app
from slack_translator.delivery.listeners import register_all
from slack_translator.delivery.listeners.context import ListenerContext
from slack_translator.delivery.settings import Settings
from slack_translator.delivery.views.presenter import SlackReadPresenter
from slack_translator.domain.ports import Clock, LanguageClassifier, Telemetry, Translator
from slack_translator.infrastructure.slack_gateway import SlackGateway
from slack_translator.infrastructure.system_clock import SystemClock
from slack_translator.infrastructure.telemetry import LogTelemetry

SLACK_API = "https://slack.com/api/"
EMAIL_SCOPE = "users:read.email"
SHUTDOWN_GRACE = 10.0


@dataclass(frozen=True, slots=True)
class Overrides:
    """Replace adapters, for tests (TS-04) and for pointing at a fake Slack API."""

    classifier: LanguageClassifier | None = None
    translator: Translator | None = None
    telemetry: Telemetry | None = None
    clock: Clock | None = None
    log_guard: Callable[[], None] | None = None
    slack_api_base_url: str = SLACK_API


class MissingScope(RuntimeError):
    """The Slack token lacks a scope the bot cannot work without."""


@runtime_checkable
class _Preloadable(Protocol):
    async def preload(self) -> None: ...


@runtime_checkable
class _Closeable(Protocol):
    async def aclose(self) -> None: ...


class _Authorization:
    """What Bolt needs to authorize a request, known after `auth.test` at startup.

    Handing it over keeps Bolt from calling Slack before the acknowledgement (REQ-01).
    """

    def __init__(self, bot_token: str) -> None:
        self._bot_token = bot_token
        self.result: AuthorizeResult | None = None

    def set(self, team_id: str, bot_id: str | None, bot_user_id: str | None) -> None:
        self.result = AuthorizeResult(
            enterprise_id=None,
            team_id=team_id,
            bot_token=self._bot_token,
            bot_id=bot_id,
            bot_user_id=bot_user_id,
        )


class Application:
    def __init__(
        self,
        settings: Settings,
        bolt: AsyncApp,
        client: AsyncWebClient,
        gateway: SlackGateway,
        home: HomeWorkspace,
        registry: TaskRegistry,
        readiness: Readiness,
        telemetry: Telemetry,
        authorization: _Authorization,
        listener_names: tuple[str, ...],
        preloads: list[Callable[[], Awaitable[None]]],
        closers: list[Callable[[], Awaitable[None]]],
        log_guard: Callable[[], None],
    ) -> None:
        self.settings = settings
        self.bolt = bolt
        self.registry = registry
        self.readiness = readiness
        self.listener_names = listener_names
        self._client = client
        self._gateway = gateway
        self._home = home
        self._telemetry = telemetry
        self._authorization = authorization
        self._preloads = preloads
        self._closers = closers
        self._log_guard = log_guard

    async def prepare(self) -> None:
        """Load models, then `auth.test` and the channel checks. Fails if the workspace is unknown.

        Order follows REQ-11.
        """
        for preload in self._preloads:
            await preload()
        auth = await self._client.auth_test()
        self._require_email_scope(auth)
        self._home.team_id = str(auth["team_id"])
        self._authorization.set(self._home.team_id, auth.get("bot_id"), auth.get("user_id"))
        for channel in self.settings.channel_allowlist:
            if not await self._bot_is_member(channel):
                self._telemetry.startup_channel_not_member(channel_id=channel)

    @staticmethod
    def _require_email_scope(auth: object) -> None:
        """Without `users:read.email` every lookup lacks the email and every member is refused in
        silence, so the bot does not start (REQ-11). When Slack does not say which scopes the
        token has, it starts anyway and the runtime check still fails closed.
        """
        headers = getattr(auth, "headers", None) or {}
        scopes = {key.lower(): value for key, value in headers.items()}.get("x-oauth-scopes")
        if scopes is not None and EMAIL_SCOPE not in {s.strip() for s in str(scopes).split(",")}:
            raise MissingScope("the bot token lacks the users:read.email scope")

    def mark_ready(self) -> None:
        """Ready means able to receive: only called once the transport is connected (REQ-11)."""
        self.readiness.ready = True
        self._telemetry.startup_ready()

    async def run(self) -> None:
        self._log_guard()
        await self.prepare()
        registry = getattr(self._telemetry, "registry", None)
        runner = web.AppRunner(build_health_app(self.readiness, registry))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.settings.port).start()
        handler = AsyncSocketModeHandler(
            self.bolt, self.settings.slack_app_token.get_secret_value()
        )
        await handler.connect_async()
        self.readiness.transport_connected = handler.client.is_connected
        self.mark_ready()

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()

        self.readiness.ready = False
        await handler.close_async()
        await self.registry.drain(within=SHUTDOWN_GRACE)
        await runner.cleanup()
        await self.close()
        self._telemetry.shutdown()

    async def close(self) -> None:
        for closer in self._closers:
            with contextlib.suppress(Exception):
                await closer()

    async def _bot_is_member(self, channel: str) -> bool:
        try:
            info = await self._client.conversations_info(channel=channel)
        except SlackApiError:
            return False
        return bool((info.get("channel") or {}).get("is_member"))


def _log_guard_of(telemetry: Telemetry) -> Callable[[], None]:
    """The third-party log guard (LOG-03). Startup fails if the telemetry cannot provide one."""
    if isinstance(telemetry, LogTelemetry):
        return telemetry.guard_third_party_loggers
    raise TypeError("this telemetry adapter has no log guard; pass Overrides.log_guard")


def create_app(settings: Settings, overrides: Overrides | None = None) -> Application:
    overrides = overrides or Overrides()
    clock = overrides.clock or SystemClock()
    telemetry = overrides.telemetry or adapters.build_telemetry(settings)
    classifier = overrides.classifier or adapters.build_classifier(settings, telemetry, clock)
    translator = overrides.translator or adapters.build_translator(settings, telemetry, clock)

    client = AsyncWebClient(
        token=settings.slack_bot_token.get_secret_value(),
        base_url=overrides.slack_api_base_url,
        timeout=5,
    )
    gateway = SlackGateway(client, telemetry, clock)

    home = HomeWorkspace()
    policy = TranslationPolicy(
        allowlist=settings.channel_allowlist,
        languages=settings.language_seed,
        read_language=settings.read_language,
        min_confidence=settings.classifier_min_confidence,
        classifier_timeout=settings.classifier_timeout,
        translator_timeout=settings.translator_timeout,
        member_email_domains=settings.member_email_domains,
    )
    translate_message = TranslateMessage(
        policy=policy,
        home=home,
        classifier=classifier,
        translator=translator,
        responder=gateway,
        reader=gateway,
        directory=gateway,
        presenter=SlackReadPresenter(policy.read_language_name),
        telemetry=telemetry,
    )

    registry = TaskRegistry()
    authorization = _Authorization(settings.slack_bot_token.get_secret_value())

    async def authorize() -> AuthorizeResult:
        if authorization.result is None:
            raise RuntimeError("the bot has not finished starting")
        return authorization.result

    bolt = AsyncApp(
        client=client,
        authorize=authorize,
        process_before_response=False,
        request_verification_enabled=False,
    )

    @bolt.error
    async def on_error(error: Exception) -> None:
        telemetry.internal_error(exception_type=type(error).__name__, correlation_id=None)

    listener_names = register_all(
        bolt,
        ListenerContext(
            translate_message=translate_message,
            registry=registry,
            telemetry=telemetry,
            clock=clock,
        ),
    )
    log_guard = overrides.log_guard or _log_guard_of(telemetry)
    preloads: list[Callable[[], Awaitable[None]]] = []
    if isinstance(classifier, _Preloadable):
        preloads.append(classifier.preload)
    closers: list[Callable[[], Awaitable[None]]] = [gateway.aclose]
    closers += [c.aclose for c in (classifier, translator) if isinstance(c, _Closeable)]
    return Application(
        settings,
        bolt,
        client,
        gateway,
        home,
        registry,
        Readiness(),
        telemetry,
        authorization,
        listener_names,
        preloads,
        closers,
        log_guard,
    )
