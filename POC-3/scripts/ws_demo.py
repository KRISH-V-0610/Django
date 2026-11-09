"""
Topic A: talks to the WebSocket endpoints of the running server.

    uv run python scripts/ws_demo.py
"""
import asyncio

import websockets

from common import BASE_URL

WS_URL = BASE_URL.replace("http", "ws", 1)  # http -> ws, https -> wss


async def echo_demo():
    print("--- /ws/echo ---")
    async with websockets.connect(f"{WS_URL}/ws/echo") as ws:
        for text in ("hello", "asgi"):
            await ws.send(text)
            print(f"sent {text!r:8} received {await ws.recv()!r}")


async def chat_demo():
    print("\n--- /ws/chat (two users) ---")
    async with websockets.connect(f"{WS_URL}/ws/chat?name=alice") as alice:
        print("alice sees:", await alice.recv())

        async with websockets.connect(f"{WS_URL}/ws/chat?name=bob") as bob:
            print("alice sees:", await alice.recv())
            print("bob sees:  ", await bob.recv())

            await bob.send("hi alice!")
            print("alice sees:", await alice.recv())
            print("bob sees:  ", await bob.recv())

        print("alice sees:", await alice.recv())  # bob left


async def main():
    await echo_demo()
    await chat_demo()


asyncio.run(main())
