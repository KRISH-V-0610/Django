"""
A pure ASGI middleware (topic A).

An ASGI app is just:  async def app(scope, receive, send).
A middleware is an app that wraps another app, so it can look at the request on the
way in (scope) and at every response message on the way out (by wrapping `send`).
"""
import logging
import time

logger = logging.getLogger(__name__)


class TimingMiddleware:
    """Adds an `X-Process-Time-Ms` header to every HTTP response and logs the duration."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # websocket / lifespan pass straight through
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - started) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time-ms", f"{elapsed_ms:.1f}".encode()))
                message = {**message, "headers": headers}
                logger.info("%s %s -> %s in %.1f ms", scope["method"], scope["path"],
                            message["status"], elapsed_ms)
            await send(message)

        await self.app(scope, receive, send_with_header)
