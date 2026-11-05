"""Request bodies are bounded for both declared and streamed transfers."""

import json

import pytest

from app.core.request_limits import MaxRequestSizeMiddleware


def _scope(headers=()):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 1000),
        "server": ("test", 80),
    }


async def _invoke(*, chunks, headers=(), max_bytes=5):
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    sent = []
    received = bytearray()

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    async def app(_scope, receive, send):
        while True:
            message = await receive()
            received.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = MaxRequestSizeMiddleware(app, max_bytes=max_bytes)
    await middleware(_scope(headers), receive, send)
    return sent, bytes(received)


@pytest.mark.asyncio
async def test_declared_oversize_body_is_rejected_before_reading() -> None:
    sent, received = await _invoke(
        chunks=[b"ignored"], headers=[(b"content-length", b"6")]
    )
    assert sent[0]["status"] == 413
    assert received == b""
    assert json.loads(sent[1]["body"])["detail"] == "Request body is too large"


@pytest.mark.asyncio
async def test_chunked_oversize_body_is_stopped_while_streaming() -> None:
    sent, received = await _invoke(chunks=[b"123", b"456"])
    assert sent[0]["status"] == 413
    assert received == b"123"


@pytest.mark.asyncio
async def test_body_at_limit_reaches_application_unchanged() -> None:
    sent, received = await _invoke(
        chunks=[b"12", b"345"], headers=[(b"content-length", b"5")]
    )
    assert sent[0]["status"] == 204
    assert received == b"12345"


@pytest.mark.asyncio
async def test_invalid_content_length_is_rejected() -> None:
    sent, _received = await _invoke(
        chunks=[b""], headers=[(b"content-length", b"invalid")]
    )
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"] == "Invalid Content-Length header"
