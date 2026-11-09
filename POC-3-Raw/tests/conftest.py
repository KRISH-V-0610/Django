"""Shared fixtures. Nothing here needs Redis, Jaeger or Docker: Redis is faked, spans stay in memory."""
import fakeredis
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from accounts.tokens import create_access_token
from jobs import streams

# A real tracer provider for the whole run (OpenTelemetry allows setting it only once).
_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def spans():
    """The finished spans of the current test: spans.get_finished_spans()."""
    _exporter.clear()
    return _exporter


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()  # login-lockout counters live in the cache
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def redis_server(monkeypatch):
    """Every test gets its own empty fake Redis; the code under test uses it through streams.get_redis()."""
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(streams, "_client", client)
    return client


@pytest.fixture(autouse=True)
def empty_chat():
    from core import websocket

    websocket.chat_clients.clear()
    yield
    websocket.chat_clients.clear()


@pytest.fixture
def make_user(db):
    User = get_user_model()

    def _make(username, password="a-long-test-password", **extra):
        return User.objects.create_user(username=username, password=password, **extra)

    return _make


@pytest.fixture
def auth():
    """auth(user) -> kwargs for the Django test client: client.get(url, **auth(user))."""

    def _auth(user):
        return {"HTTP_AUTHORIZATION": f"Bearer {create_access_token(user.id)}"}

    return _auth


@pytest.fixture
def alice(make_user):
    return make_user("alice")


@pytest.fixture
def bob(make_user):
    return make_user("bob")
