"""
ASGI lifespan (topic A): the server tells the app "you are starting" / "you are stopping".

Uvicorn sends two events on a scope of type "lifespan":
    lifespan.startup   -> do one-time set-up, then reply lifespan.startup.complete
    lifespan.shutdown  -> clean up, then reply lifespan.shutdown.complete
Django's own ASGI handler ignores lifespan, so we answer it ourselves.
"""
import logging

from .telemetry import shutdown_telemetry

logger = logging.getLogger(__name__)


async def lifespan_app(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            logger.info("lifespan: startup")
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            logger.info("lifespan: shutdown (flushing traces)")
            shutdown_telemetry()
            await send({"type": "lifespan.shutdown.complete"})
            return
