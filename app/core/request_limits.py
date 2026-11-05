"""ASGI request limits applied before request bodies are buffered or parsed."""

from __future__ import annotations

from starlette.responses import JSONResponse


class _RequestBodyTooLarge(Exception):
    pass


class MaxRequestSizeMiddleware:
    """Reject declared and streamed HTTP bodies above ``max_bytes``."""

    def __init__(self, app, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await self._reject(scope, receive, send, "Invalid Content-Length header")
                return
            if declared < 0:
                await self._reject(scope, receive, send, "Invalid Content-Length header")
                return
            if declared > self.max_bytes:
                await self._reject(scope, receive, send)
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            # Body parsing normally precedes a response. If an application
            # streams a response before consuming the request, closing it is
            # safer than attempting to send a second response start.
            if not response_started:
                await self._reject(scope, receive, send)

    async def _reject(self, scope, receive, send, detail="Request body is too large") -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": detail},
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)
