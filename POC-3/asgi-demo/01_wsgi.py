from wsgiref.simple_server import make_server

def app(environ, start_response):
    path = environ["PATH_INFO"]
    body = f"Hello, you asked for {path}".encode()
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [body]

with make_server("", 8000, app) as server:
    print("Serving on http://127.0.0.1:8000")
    server.serve_forever()
