"""FLOW-02 steps 5 to 7 · One private offer per eligible member of the channel.

Eligible means an X3M member (decision 23, the same `membership` rule as the message menu) other
than the author. A member whose lookup fails gets no offer. Offers go out at most four at a time,
wait out a rate limit together, and are dropped a minute after the event. Customers never receive
anything: they are never members. Nothing here can raise, and no event carries a person or text.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from slack_translator.actions.translate_message import HomeWorkspace, TranslationPolicy
from slack_translator.domain.action_values import ActionValue, encode
from slack_translator.domain.boundary import membership
from slack_translator.domain.errors import SlackDeliveryError
from slack_translator.domain.messages import Offer, OfferRequest, PrivateMessage
from slack_translator.domain.ports import (
    Clock,
    MemberDirectory,
    OfferOutcome,
    PrivateResponder,
    ReadPresenter,
    Telemetry,
)

OFFER_LIFETIME: Final = 60.0
MAX_IN_FLIGHT: Final = 4


@dataclass
class _Pause:
    """When the next offer may go out. A rate limit on one offer holds back all of them."""

    resume_at: float = 0.0


class SendOffers:
    def __init__(
        self,
        policy: TranslationPolicy,
        home: HomeWorkspace,
        directory: MemberDirectory,
        responder: PrivateResponder,
        presenter: ReadPresenter,
        telemetry: Telemetry,
        clock: Clock,
    ) -> None:
        self._policy = policy
        self._home = home
        self._directory = directory
        self._responder = responder
        self._presenter = presenter
        self._telemetry = telemetry
        self._clock = clock

    async def send(self, request: OfferRequest) -> None:
        recipients: list[str] = []
        try:
            recipients = await self._recipients(request)
        except Exception as error:
            self._unexpected(request, error)

        outcomes = await self._offer_all(request, recipients)
        self._telemetry.offer_finished(
            correlation_id=request.correlation_id,
            channel_id=request.ref.channel,
            message_ts=request.ref.ts,
            sent=outcomes.count("sent"),
            failed=outcomes.count("failed"),
            expired=outcomes.count("expired"),
        )

    async def _recipients(self, request: OfferRequest) -> list[str]:
        home = self._home.team_id
        if home is None:
            return []
        members = await self._directory.channel_members(request.ref.channel)
        candidates = [user for user in members if user != request.author_id]
        gate = asyncio.Semaphore(MAX_IN_FLIGHT)

        async def is_member(user: str) -> bool:
            async with gate:
                try:
                    facts = await self._directory.user_facts(user)
                except Exception:
                    return False
            return membership(facts, home, self._policy.member_email_domains) == "member"

        verdicts = await asyncio.gather(*(is_member(user) for user in candidates))
        return [user for user, ok in zip(candidates, verdicts, strict=True) if ok]

    async def _offer_all(
        self, request: OfferRequest, recipients: Sequence[str]
    ) -> list[OfferOutcome]:
        if not recipients:
            return []
        message = self._offer_message(request)
        if message is None:
            return ["failed"] * len(recipients)

        deadline = request.received_at + OFFER_LIFETIME
        gate = asyncio.Semaphore(MAX_IN_FLIGHT)
        pause = _Pause()

        async def offer(user: str) -> OfferOutcome:
            async with gate:
                return await self._offer_one(request, user, message, deadline, pause)

        return list(await asyncio.gather(*(offer(user) for user in recipients)))

    async def _offer_one(
        self,
        request: OfferRequest,
        user: str,
        message: PrivateMessage,
        deadline: float,
        pause: _Pause,
    ) -> OfferOutcome:
        while True:
            now = self._clock.now()
            if now >= deadline:
                return "expired"
            wait = pause.resume_at - now
            if wait > 0:
                if now + wait >= deadline:
                    return "expired"
                await self._clock.sleep(wait)
                continue
            try:
                await self._responder.ephemeral(
                    request.ref.channel, user, message, request.ref.thread_root_ts
                )
            except SlackDeliveryError as error:
                if error.retry_after is None:
                    return "failed"
                pause.resume_at = max(pause.resume_at, self._clock.now() + error.retry_after)
            except Exception as error:
                self._unexpected(request, error)
                return "failed"
            else:
                return "sent"

    def _offer_message(self, request: OfferRequest) -> PrivateMessage | None:
        source = next(
            (lang for lang in self._policy.languages if lang.code == request.language), None
        )
        if source is None:
            self._telemetry.internal_error(
                exception_type="UnknownLanguage", correlation_id=request.correlation_id
            )
            return None
        value = ActionValue(
            channel=request.ref.channel,
            ts=request.ref.ts,
            thread_root_ts=request.ref.thread_root_ts,
            language=request.language,
            script=request.script,
        )
        return self._presenter.present(
            Offer(
                source_language=source.name,
                reading_language=self._policy.read_language_name,
                action_value=encode(value),
            )
        )

    def _unexpected(self, request: OfferRequest, error: Exception) -> None:
        self._telemetry.internal_error(
            exception_type=type(error).__name__, correlation_id=request.correlation_id
        )
