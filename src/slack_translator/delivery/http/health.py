"""ADR-002 · One small web server from day one: /healthz, /readyz and /metrics."""

from collections.abc import Awaitable, Callable

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest


class Readiness:
    """Ready only after startup finished and the Slack connection is up (REQ-11).

    `transport_connected` is asked on every probe, so readiness drops when the connection does.
    """

    def __init__(self) -> None:
        self.ready = False
        self.transport_connected: Callable[[], Awaitable[bool]] | None = None

    async def is_ready(self) -> bool:
        if not self.ready:
            return False
        if self.transport_connected is None:
            return True
        try:
            return await self.transport_connected()
        except Exception:
            return False


def build_health_app(
    readiness: Readiness, registry: CollectorRegistry | None = None
) -> web.Application:
    async def healthz(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def readyz(_: web.Request) -> web.Response:
        if await readiness.is_ready():
            return web.Response(text="ready")
        return web.Response(text="starting", status=503)

    async def metrics(_: web.Request) -> web.Response:
        body = generate_latest(registry) if registry is not None else generate_latest()
        return web.Response(body=body, headers={"Content-Type": CONTENT_TYPE_LATEST})

    app = web.Application()
    app.add_routes(
        [web.get("/healthz", healthz), web.get("/readyz", readyz), web.get("/metrics", metrics)]
    )
    return app
