import asyncio

async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    # message 1: status and headers
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })

    # messages 2 to 4: body in pieces, one per second
    for i in range(1, 4):
        await send({
            "type": "http.response.body",
            "body": f"chunk {i}\n".encode(),
            "more_body": True,        # "more is coming"
        })
        await asyncio.sleep(1)        # waiting, so the loop can serve others

    # last message: no more_body, so the response is finished
    await send({"type": "http.response.body", "body": b"done\n"})
