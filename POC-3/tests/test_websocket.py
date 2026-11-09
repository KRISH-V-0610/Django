"""
Topic A: WebSocket handling written directly against the ASGI protocol.

FakeSocket plays the role of the browser: it sends the raw ASGI events a server would
deliver (websocket.connect / receive / disconnect) and reads what the app sends back.
"""
from urllib.parse import urlsplit

from asgiref.testing import ApplicationCommunicator

from config.asgi import application
from core import websocket


class FakeSocket:
    def __init__(self, url):
        parts = urlsplit(url)
        scope = {
            "type": "websocket",
            "path": parts.path,
            "query_string": parts.query.encode(),
            "headers": [],
        }
        self.app = ApplicationCommunicator(application, scope)

    async def connect(self):
        """Returns (accepted, close_code)."""
        await self.app.send_input({"type": "websocket.connect"})
        reply = await self.app.receive_output(timeout=2)
        if reply["type"] == "websocket.accept":
            return True, None
        assert reply["type"] == "websocket.close"
        return False, reply["code"]

    async def send(self, text):
        await self.app.send_input({"type": "websocket.receive", "text": text})

    async def receive(self):
        message = await self.app.receive_output(timeout=2)
        assert message["type"] == "websocket.send"
        return message["text"]

    async def disconnect(self):
        await self.app.send_input({"type": "websocket.disconnect", "code": 1000})
        await self.app.wait(timeout=2)  # the app function returned


async def test_echo_replies_with_the_same_text():
    ws = FakeSocket("/ws/echo")
    assert await ws.connect() == (True, None)

    await ws.send("hello")
    assert await ws.receive() == "echo: hello"
    await ws.send("again")
    assert await ws.receive() == "echo: again"

    await ws.disconnect()


async def test_unknown_websocket_path_is_closed_with_4404():
    ws = FakeSocket("/ws/nothing-here")
    assert await ws.connect() == (False, 4404)


async def test_chat_broadcasts_to_everyone():
    alice = FakeSocket("/ws/chat?name=alice")
    bob = FakeSocket("/ws/chat?name=bob")

    assert (await alice.connect())[0]
    assert await alice.receive() == "* alice joined"

    assert (await bob.connect())[0]
    assert await alice.receive() == "* bob joined"  # alice sees bob arrive
    assert await bob.receive() == "* bob joined"

    await bob.send("hi alice")
    assert await alice.receive() == "bob: hi alice"
    assert await bob.receive() == "bob: hi alice"

    await bob.disconnect()
    assert await alice.receive() == "* bob left"
    await alice.disconnect()


async def test_chat_client_is_removed_on_disconnect():
    alice = FakeSocket("/ws/chat?name=alice")
    bob = FakeSocket("/ws/chat?name=bob")
    await alice.connect()
    await alice.receive()
    await bob.connect()
    await alice.receive()
    await bob.receive()
    assert len(websocket.chat_clients) == 2

    await bob.disconnect()
    assert await alice.receive() == "* bob left"
    assert len(websocket.chat_clients) == 1

    await alice.disconnect()
    assert len(websocket.chat_clients) == 0


async def test_chat_name_defaults_to_anonymous():
    ws = FakeSocket("/ws/chat")
    await ws.connect()
    assert await ws.receive() == "* anonymous joined"
    await ws.disconnect()
