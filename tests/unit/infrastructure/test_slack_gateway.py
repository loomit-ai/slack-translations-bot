"""IF-05 · Response URL limits and failures. The Slack Web API paths are covered end to end in
tests/integration, against the fake Slack server."""

import json

import httpx
import pytest
from slack_sdk.web.async_client import AsyncWebClient
from tests.support.fake_ports import FakeClock, RecordingTelemetry

from slack_translator.domain.errors import ResponseUrlUnusable
from slack_translator.domain.messages import PrivateMessage, ResponseUrl
from slack_translator.infrastructure.slack_gateway import (
    RESPONSE_URL_LIFETIME,
    RESPONSE_URL_MAX_USES,
    SlackGateway,
    email_domain_of,
)

MESSAGE = PrivateMessage(text="hello", blocks=[])
URL = ResponseUrl("https://hooks.slack.test/actions/T0HOME/1/abc")


def gateway(
    handler: httpx.MockTransport | None = None, clock: FakeClock | None = None
) -> tuple[SlackGateway, list[httpx.Request], RecordingTelemetry]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    http = httpx.AsyncClient(transport=handler or httpx.MockTransport(respond))
    telemetry = RecordingTelemetry()
    return SlackGateway(AsyncWebClient(), telemetry, clock or FakeClock(), http), seen, telemetry


async def test_a_response_url_stops_working_after_its_lifetime() -> None:
    clock = FakeClock()
    slack, seen, _ = gateway(clock=clock)
    await slack.via_response_url(URL, MESSAGE, "new")
    clock.advance(RESPONSE_URL_LIFETIME)
    with pytest.raises(ResponseUrlUnusable):
        await slack.via_response_url(URL, MESSAGE, "replace")
    assert len(seen) == 1


async def test_a_response_url_stops_working_after_its_uses() -> None:
    slack, seen, _ = gateway()
    for _ in range(RESPONSE_URL_MAX_USES):
        await slack.via_response_url(URL, MESSAGE, "new")
    with pytest.raises(ResponseUrlUnusable):
        await slack.via_response_url(URL, MESSAGE, "new")
    assert len(seen) == RESPONSE_URL_MAX_USES


@pytest.mark.parametrize("status", [301, 302, 307, 404, 410, 429, 500])
async def test_slack_refusing_a_response_url_makes_it_unusable(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="used_url"))
    slack, _, telemetry = gateway(transport)
    with pytest.raises(ResponseUrlUnusable):
        await slack.via_response_url(URL, MESSAGE, "new")
    assert telemetry.named("slack_call")[0].fields["outcome"] in ("error", "rate_limited")


async def test_a_response_url_that_cannot_be_reached_is_unusable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    slack, _, telemetry = gateway(httpx.MockTransport(fail))
    with pytest.raises(ResponseUrlUnusable):
        await slack.via_response_url(URL, MESSAGE, "new")
    assert telemetry.named("slack_call")[0].fields["outcome"] == "timeout"


async def test_the_three_modes_send_the_right_flags_and_always_stay_private() -> None:
    slack, seen, _ = gateway()
    await slack.via_response_url(ResponseUrl("https://hooks.slack.test/a"), MESSAGE, "new", "1.1")
    await slack.via_response_url(
        ResponseUrl("https://hooks.slack.test/b"), MESSAGE, "replace", "1.1"
    )
    await slack.via_response_url(ResponseUrl("https://hooks.slack.test/c"), MESSAGE, "delete")
    new, replace, delete = (json.loads(request.read()) for request in seen)
    assert new["response_type"] == "ephemeral"
    assert new["replace_original"] is False
    assert new["thread_ts"] == "1.1"
    assert replace["response_type"] == "ephemeral"
    assert replace["replace_original"] is True
    assert "thread_ts" not in replace
    assert delete == {"delete_original": True}


async def test_the_response_url_never_appears_in_telemetry() -> None:
    slack, _, telemetry = gateway(httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ResponseUrlUnusable):
        await slack.via_response_url(URL, MESSAGE, "new")
    assert URL.reveal() not in repr(telemetry.events)


@pytest.mark.parametrize(
    ("address", "domain"),
    [
        ("m1@x3mads.com", "x3mads.com"),
        ("M1@X3MADS.COM", "x3mads.com"),
        ("  m1@loomit.ai  ", "loomit.ai"),
        ("first.last+tag@mail.x3mads.com", "mail.x3mads.com"),
        ("x3mads.com", None),
        ("m1@@x3mads.com", None),
        ("a@b@x3mads.com", None),
        ("@x3mads.com", None),
        ("m1@", None),
        ("", None),
        ("   ", None),
        ("m 1@x3mads.com", None),
        ("m1@x3mads .com", None),
        (None, None),
        (42, None),
    ],
)
def test_only_the_domain_is_taken_from_an_address(address: object, domain: str | None) -> None:
    assert email_domain_of(address) == domain
