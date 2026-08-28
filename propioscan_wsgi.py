"""Synchronous WSGI entry point for the CloudLinux/LiteSpeed runtime.

LiteSpeed preloads the application and then forks its LSAPI workers. Adapters
that keep an asyncio loop in a background thread therefore inherit a dead
thread after the fork. This bridge creates a fresh event loop for each WSGI
request in the worker that actually handles it, so it is safe with multiple
LiteSpeed workers.
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any, Callable, Iterable

from app.main import app


class SynchronousASGIMiddleware:
    """Run one HTTP ASGI exchange synchronously inside a WSGI worker."""

    def __init__(self, asgi_app: Any) -> None:
        self.asgi_app = asgi_app

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
        body = _read_request_body(environ)
        scope = _build_scope(environ)
        response_status: int | None = None
        response_headers: list[tuple[bytes, bytes]] = []
        response_body: list[bytes] = []
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal response_status, response_headers
            message_type = message["type"]
            if message_type == "http.response.start":
                response_status = int(message["status"])
                response_headers = list(message.get("headers", []))
            elif message_type == "http.response.body":
                response_body.append(message.get("body", b""))

        asyncio.run(self.asgi_app(scope, receive, send))
        if response_status is None:
            raise RuntimeError("ASGI application did not start an HTTP response")

        try:
            reason = HTTPStatus(response_status).phrase
        except ValueError:
            reason = "Unknown Status"
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in response_headers
        ]
        start_response(f"{response_status} {reason}", headers)
        return response_body


def _read_request_body(environ: dict[str, Any]) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


def _build_scope(environ: dict[str, Any]) -> dict[str, Any]:
    script_name = environ.get("SCRIPT_NAME", "")
    path_info = environ.get("PATH_INFO", "")
    server_protocol = environ.get("SERVER_PROTOCOL", "HTTP/1.1")
    http_version = server_protocol.split("/", 1)[-1]
    server_port = _integer_or_default(environ.get("SERVER_PORT"), 443)
    client_port = _integer_or_default(environ.get("REMOTE_PORT"), 0)

    headers: list[tuple[bytes, bytes]] = []
    if environ.get("CONTENT_TYPE"):
        headers.append((b"content-type", environ["CONTENT_TYPE"].encode("latin-1")))
    if environ.get("CONTENT_LENGTH"):
        headers.append(
            (b"content-length", str(environ["CONTENT_LENGTH"]).encode("latin-1"))
        )
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").lower().encode("ascii")
            headers.append((name, str(value).encode("latin-1")))

    full_path = f"{script_name}{path_info}" or "/"
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": http_version,
        "method": environ.get("REQUEST_METHOD", "GET"),
        "scheme": environ.get("wsgi.url_scheme", "http"),
        "path": full_path,
        "raw_path": full_path.encode("utf-8"),
        "root_path": script_name,
        "query_string": environ.get("QUERY_STRING", "").encode("latin-1"),
        "headers": headers,
        "client": (environ.get("REMOTE_ADDR", ""), client_port),
        "server": (environ.get("SERVER_NAME", "localhost"), server_port),
        "state": {},
    }


def _integer_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


application = SynchronousASGIMiddleware(app)
