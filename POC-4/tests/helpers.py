import asyncio


async def wait_until(condition, seconds: float = 10.0) -> None:
    """Poll (in real time) until condition() is true. Used to wait for a workflow to reach a step."""
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not met in time")


async def wait_for_status(handle, query, wanted: str, seconds: float = 10.0) -> None:
    """Poll the workflow's `status` query until it reports the wanted status."""
    last = None
    for _ in range(int(seconds / 0.05)):
        last = (await handle.query(query))["status"]
        if last == wanted:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached {wanted}, last status was {last}")
