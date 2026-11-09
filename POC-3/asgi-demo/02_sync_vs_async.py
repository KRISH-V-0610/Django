import asyncio
import time

# ---------- 1. SYNC: wait for each one, one after another
def fetch_sync(name):
    print(f"{name}: start")
    time.sleep(1)             # waiting (pretend: DB or API call)
    print(f"{name}: done")

def run_sync():
    start = time.time()
    for name in ["A", "B", "C"]:
        fetch_sync(name)
    print("SYNC took", round(time.time() - start, 1), "s\n")

# ---------- 2. ASYNC: while one waits, the others run
async def fetch_async(name):
    print(f"{name}: start")
    await asyncio.sleep(1)    # waiting, but says "run others meanwhile"
    print(f"{name}: done")

async def run_async():
    start = time.time()
    await asyncio.gather(fetch_async("A"), fetch_async("B"), fetch_async("C"))
    print("ASYNC took", round(time.time() - start, 1), "s\n")

# ---------- 3. THE TRAP: async function, but a blocking wait inside
async def fetch_blocking(name):
    print(f"{name}: start")
    time.sleep(1)             # blocks everything
    print(f"{name}: done")

async def run_trap():
    start = time.time()
    await asyncio.gather(
        fetch_blocking("A"), fetch_blocking("B"), fetch_blocking("C")
    )
    print("TRAP took", round(time.time() - start, 1), "s")

run_sync()
asyncio.run(run_async())
asyncio.run(run_trap())

