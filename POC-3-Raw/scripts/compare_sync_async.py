"""
Topic A: sends several requests AT THE SAME TIME to the sync, async and async-blocking
endpoints and prints how long each group took.

    uv run python scripts/compare_sync_async.py            (server on :8000)
    uv run python scripts/compare_sync_async.py 10 0.5     (10 requests, each waits 0.5 s)
"""
import asyncio
import sys
import time

import httpx

from common import BASE_URL

ENDPOINTS = {
    "async  (await asyncio.sleep)": "/api/demo/async",
    "sync   (def + time.sleep)": "/api/demo/sync",
    "async  (BLOCKING time.sleep)": "/api/demo/async-blocking",
}


async def run(path, count, seconds):
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as http:
        started = time.perf_counter()
        responses = await asyncio.gather(*[http.get(path, params={"seconds": seconds}) for _ in range(count)])
        elapsed = time.perf_counter() - started
    failed = [r for r in responses if r.status_code != 200]
    if failed:
        raise SystemExit(f"{path} answered {failed[0].status_code}: is the server running?")
    return elapsed


async def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    print(f"{count} concurrent requests, each one waits {seconds}s")
    print(f"one after another it would take {count * seconds:.1f}s\n")
    for label, path in ENDPOINTS.items():
        elapsed = await run(path, count, seconds)
        print(f"{label:<32} total {elapsed:5.2f}s")
    print("\nsync and async both overlap (sync views run in worker threads under ASGI).")
    print("The blocking call inside `async def` is the one that stops the whole event loop.")


asyncio.run(main())
