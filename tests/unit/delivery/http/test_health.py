"""ADR-002 and REQ-11 · /healthz, /readyz and /metrics."""

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from prometheus_client import CollectorRegistry, Counter

from slack_translator.delivery.http.health import Readiness, build_health_app


async def get(
    readiness: Readiness, path: str, registry: CollectorRegistry | None = None
) -> tuple[int, str]:
    async with (
        TestServer(build_health_app(readiness, registry)) as server,
        aiohttp.ClientSession() as session,
        session.get(server.make_url(path)) as response,
    ):
        return response.status, await response.text()


async def test_the_process_is_alive_whatever_its_readiness() -> None:
    assert await get(Readiness(), "/healthz") == (200, "ok")


async def test_it_is_not_ready_before_startup_finished() -> None:
    assert (await get(Readiness(), "/readyz"))[0] == 503


async def test_it_is_ready_once_startup_finished_when_there_is_no_transport_to_ask() -> None:
    readiness = Readiness()
    readiness.ready = True
    assert await get(readiness, "/readyz") == (200, "ready")


@pytest.mark.parametrize(("connected", "status"), [(True, 200), (False, 503)])
async def test_readiness_follows_the_slack_connection(connected: bool, status: int) -> None:
    readiness = Readiness()
    readiness.ready = True

    async def transport_connected() -> bool:
        return connected

    readiness.transport_connected = transport_connected
    assert (await get(readiness, "/readyz"))[0] == status


async def test_a_connection_check_that_fails_means_not_ready() -> None:
    readiness = Readiness()
    readiness.ready = True

    async def broken() -> bool:
        raise RuntimeError("socket gone")

    readiness.transport_connected = broken
    assert (await get(readiness, "/readyz"))[0] == 503


async def test_metrics_come_from_the_registry_it_was_given() -> None:
    registry = CollectorRegistry()
    Counter("translator_test_total", "A test counter", registry=registry).inc()
    status, body = await get(Readiness(), "/metrics", registry)
    assert status == 200
    assert "translator_test_total 1.0" in body
