"""
Topic A: sync vs async views under ASGI. Five requests that each wait 0.3 s are sent at the same time.

  async def              -> the event loop serves all five while they wait:  ~0.3 s in total
  def                    -> Django runs each request's sync code in its own
                            worker thread, so they overlap too:              ~0.3 s in total
  async def + time.sleep -> blocks the event loop itself, nothing else can
                            run until it is done:                            ~1.5 s in total
"""
import asyncio
import time

import httpx

from config.asgi import application

PAUSE = 0.3
REQUESTS = 5
SERIAL_TIME = PAUSE * REQUESTS


def new_client():
    # ASGITransport calls our ASGI app directly, no network and no server needed
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://localhost")


async def total_time(path):
    async with new_client() as http:
        started = time.perf_counter()
        responses = await asyncio.gather(*[http.get(path, params={"seconds": PAUSE}) for _ in range(REQUESTS)])
        elapsed = time.perf_counter() - started
    assert all(r.status_code == 200 for r in responses)
    return elapsed


async def test_async_views_overlap():
    elapsed = await total_time("/api/demo/async")
    assert elapsed < SERIAL_TIME * 0.6  # far less than 5 x 0.3 s


async def test_sync_views_overlap_too_because_they_run_in_threads():
    elapsed = await total_time("/api/demo/sync")
    assert elapsed < SERIAL_TIME * 0.6


async def test_blocking_call_inside_async_view_blocks_the_event_loop():
    elapsed = await total_time("/api/demo/async-blocking")
    assert elapsed >= SERIAL_TIME * 0.9  # the five requests ran one after another


async def test_sync_view_runs_in_a_worker_thread_async_view_on_the_loop_thread():
    async with new_client() as http:
        async_thread = (await http.get("/api/demo/async", params={"seconds": 0})).json()["thread"]
        sync_thread = (await http.get("/api/demo/sync", params={"seconds": 0})).json()["thread"]
        blocking_thread = (await http.get("/api/demo/async-blocking", params={"seconds": 0})).json()["thread"]
    assert async_thread == blocking_thread  # both are coroutines on the event loop's thread
    assert sync_thread != async_thread  # the sync view was moved to a worker thread
