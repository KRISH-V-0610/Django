"""Sync vs async endpoints (topic A) and a small traced flow (topic B)."""
import asyncio
import threading
import time

from django.contrib.auth import get_user_model
from ninja import Query, Router
from opentelemetry import trace

from .telemetry import get_tracer

router = Router(tags=["demo"])
User = get_user_model()


# --------------------------------------------------------------- A. sync vs async
@router.get("/sync")
def sync_endpoint(request, seconds: float = Query(1.0, ge=0, le=10)):
    """
    A normal `def` view. Under ASGI Django runs it in a worker thread (every request gets
    its own), so the event loop stays free. The price is one thread per request in flight.
    """
    time.sleep(seconds)
    return {"mode": "sync", "thread": threading.current_thread().name, "slept": seconds}


@router.get("/async")
async def async_endpoint(request, seconds: float = Query(1.0, ge=0, le=10)):
    """An `async def` view. While it awaits the sleep, the loop serves other requests."""
    await asyncio.sleep(seconds)
    return {"mode": "async", "thread": threading.current_thread().name, "slept": seconds}


@router.get("/async-blocking")
async def async_blocking_endpoint(request, seconds: float = Query(1.0, ge=0, le=10)):
    """
    The classic mistake: blocking code inside `async def`. time.sleep() never gives control
    back to the event loop, so EVERY other request waits. Kept to show the problem.
    """
    time.sleep(seconds)
    return {"mode": "async-blocking", "thread": threading.current_thread().name, "slept": seconds}


@router.get("/db-sync")
def db_sync(request):
    return {"mode": "sync", "users": User.objects.count()}


@router.get("/db-async")
async def db_async(request):
    """Django's async ORM: acount(), aget(), acreate(), `async for` ..."""
    return {"mode": "async", "users": await User.objects.acount()}


# --------------------------------------------------------------- B. custom spans
@router.get("/trace")
def trace_demo(request, fail: bool = False):
    """
    Builds a small tree of spans:
        POST/GET /api/demo/trace            (created by the Django instrumentation)
          └─ demo.business_flow             (ours, with attributes)
               ├─ demo.load_data            (+ the SQL query span below it)
               └─ demo.calculate
    With ?fail=true the exception is recorded on the span and the span is marked ERROR.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("demo.business_flow") as flow:
        flow.set_attribute("demo.fail_requested", fail)

        with tracer.start_as_current_span("demo.load_data") as step:
            step.set_attribute("demo.users", User.objects.count())

        with tracer.start_as_current_span("demo.calculate") as step:
            step.set_attribute("demo.items", 3)
            time.sleep(0.05)

        if fail:
            # start_as_current_span records the exception and sets the ERROR status
            # automatically when an exception leaves the `with` block.
            raise ValueError("demo failure")

        trace_id = format(trace.get_current_span().get_span_context().trace_id, "032x")
    return {"status": "ok", "trace_id": trace_id}
