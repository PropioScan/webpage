from io import BytesIO

from passenger_wsgi import application
from propioscan_wsgi import SynchronousASGIMiddleware


def test_passenger_wsgi_serves_health_endpoint() -> None:
    status: list[str] = []
    headers: list[tuple[str, str]] = []

    def start_response(
        response_status: str,
        response_headers: list[tuple[str, str]],
        exc_info=None,
    ) -> None:
        status.append(response_status)
        headers.extend(response_headers)

    environ = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/api/health",
        "QUERY_STRING": "",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }

    body = b"".join(application(environ, start_response))

    assert status == ["200 OK"]
    assert ("content-type", "application/json") in [
        (name.lower(), value.split(";", 1)[0]) for name, value in headers
    ]
    assert b'"status":"ok"' in body


def test_wsgi_bridge_creates_a_fresh_event_loop_for_each_request() -> None:
    loops: list[object] = []

    async def asgi_app(scope, receive, send) -> None:
        loops.append(__import__("asyncio").get_running_loop())
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = SynchronousASGIMiddleware(asgi_app)
    environ = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/",
        "QUERY_STRING": "",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.url_scheme": "https",
        "wsgi.input": BytesIO(b""),
    }

    assert b"".join(middleware(environ, lambda *args: None)) == b"ok"
    assert b"".join(middleware(environ, lambda *args: None)) == b"ok"
    assert len(loops) == 2
    assert loops[0] is not loops[1]
