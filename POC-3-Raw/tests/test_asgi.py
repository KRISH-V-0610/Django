"""Topic A: the ASGI application itself (lifespan, middleware, routing)."""
import httpx
import pytest
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth import get_user_model

from config.asgi import application


def client():
    # ASGITransport calls our ASGI app directly, no network and no server needed
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://localhost")


async def test_lifespan_startup_and_shutdown():
    communicator = ApplicationCommunicator(application, {"type": "lifespan"})

    await communicator.send_input({"type": "lifespan.startup"})
    assert (await communicator.receive_output(timeout=2))["type"] == "lifespan.startup.complete"

    await communicator.send_input({"type": "lifespan.shutdown"})
    assert (await communicator.receive_output(timeout=2))["type"] == "lifespan.shutdown.complete"
    await communicator.wait(timeout=2)  # the app returned: the lifespan loop ended


async def test_http_request_goes_to_django():
    async with client() as http:
        response = await http.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_timing_middleware_adds_header():
    async with client() as http:
        response = await http.get("/api/health")
    assert float(response.headers["x-process-time-ms"]) >= 0


async def test_security_headers_are_set():
    async with client() as http:
        response = await http.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


async def test_swagger_docs_page_and_openapi_schema_are_served():
    async with client() as http:
        docs = await http.get("/api/docs")
        schema = await http.get("/api/openapi.json")
    assert docs.status_code == 200
    assert schema.status_code == 200
    assert "/api/jobs" in schema.json()["paths"]


async def test_unknown_url_is_404_json_not_a_stack_trace():
    async with client() as http:
        response = await http.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text


@pytest.mark.django_db(transaction=True)
async def test_async_view_can_use_the_async_orm():
    await get_user_model().objects.acreate(username="someone")
    async with client() as http:
        response = await http.get("/api/demo/db-async")
    assert response.json() == {"mode": "async", "users": 1}
