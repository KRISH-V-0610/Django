"""
The Temporal Client: how our code (API, demos, schedules CLI) talks to the Temporal Server.
It starts workflows, sends signals, runs queries. It never runs workflow code itself.
"""
from django.conf import settings
from temporalio.client import Client

_client: Client | None = None


async def get_client() -> Client:
    """
    One client per process, created on first use. A client is bound to the event loop
    it was created on, which is why the API must run under uvicorn (one loop for the
    whole process) and not under runserver.
    """
    global _client
    if _client is None:
        _client = await Client.connect(
            settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE
        )
    return _client
