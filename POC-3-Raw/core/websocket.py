"""
WebSocket endpoints written directly against the ASGI WebSocket protocol (topic A).

Life of a connection (messages the server receives / sends):
    receive: websocket.connect     -> we must answer with websocket.accept (or websocket.close)
    receive: websocket.receive     -> {"text": "..."} or {"bytes": b"..."}  (repeats)
    send:    websocket.send        -> {"text": "..."}
    receive: websocket.disconnect  -> the client left, stop the loop

Routes:  /ws/echo         sends back whatever it receives
         /ws/chat?name=x  every message is broadcast to everyone connected to the chat
"""
import logging
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

# `send` callables of everyone in the chat. Lives in this process only: with several
# server processes you need a shared channel layer (Redis) - that is what Django Channels adds.
chat_clients = set()


async def websocket_app(scope, receive, send):
    path = scope["path"]
    if path == "/ws/echo":
        await echo(scope, receive, send)
    elif path == "/ws/chat":
        await chat(scope, receive, send)
    else:
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 4404})


async def echo(scope, receive, send):
    await receive()  # websocket.connect
    await send({"type": "websocket.accept"})
    logger.info("ws echo: connected")
    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            logger.info("ws echo: disconnected (code %s)", message.get("code"))
            return
        text = message.get("text")
        if text is not None:
            await send({"type": "websocket.send", "text": f"echo: {text}"})


async def chat(scope, receive, send):
    query = parse_qs(scope["query_string"].decode())
    name = query.get("name", ["anonymous"])[0][:30]

    await receive()  # websocket.connect
    await send({"type": "websocket.accept"})
    chat_clients.add(send)
    await broadcast(f"* {name} joined")
    try:
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text:
                await broadcast(f"{name}: {text}")
    finally:
        chat_clients.discard(send)  # always runs, even if the loop crashed
        await broadcast(f"* {name} left")


async def broadcast(text):
    for client_send in list(chat_clients):
        try:
            await client_send({"type": "websocket.send", "text": text})
        except Exception:  # that client already went away
            chat_clients.discard(client_send)
