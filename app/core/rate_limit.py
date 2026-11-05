"""Small process-local rate limits for public authentication endpoints.

The limiter deliberately keeps no persistent state. It protects each API
instance without introducing a database or schema dependency.
"""

from __future__ import annotations

from collections import defaultdict, deque
from ipaddress import ip_address
import os
from threading import Lock
import time

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    def __init__(self, *, max_requests: int, window_seconds: int) -> None:
        if max_requests < 1 or window_seconds < 1:
            raise ValueError("Rate-limit bounds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()

    def check_key(self, key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.max_requests:
                retry_after = max(1, int(attempts[0] + self.window_seconds - current) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            attempts.append(current)

    def enforce(self, request: Request, *, subject: str = "") -> None:
        # The test suite has its own deterministic limiter tests. Disabling the
        # shared counters prevents unrelated integration cases from influencing
        # one another through TestClient's common source address.
        if os.getenv("ELETTRA_TESTING") == "1":
            return
        self.check_key(f"{_client_address(request)}:{subject.casefold()}")


def _client_address(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-real-ip", "").strip()
    if not forwarded:
        return peer
    try:
        peer_ip = ip_address(peer)
        forwarded_ip = ip_address(forwarded)
    except ValueError:
        return peer
    # Only trust the address inserted by the reverse proxy when the immediate
    # peer is on a private/loopback network. A direct public client cannot spoof
    # its limiter identity with this header.
    if peer_ip.is_private or peer_ip.is_loopback:
        return str(forwarded_ip)
    return peer
