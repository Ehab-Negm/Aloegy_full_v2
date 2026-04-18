"""HTTP client primitives — shared httpx client, retry delay, response helpers."""
from __future__ import annotations

import asyncio
import os
import re

import httpx


# Module-level singleton client
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


def _reset_http_client_after_fork() -> None:
    """Reset the singleton after fork so the child process gets a fresh client.

    The parent's httpx client holds socket state that is not safe to share
    across forked processes.  The asyncio.Lock is also bound to the parent's
    event loop and invalid in the child.
    """
    global _http_client, _http_client_lock
    _http_client = None
    _http_client_lock = asyncio.Lock()


# register_at_fork is only available on platforms that support fork (Linux/macOS)
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_http_client_after_fork)


async def get_http_client(
    *,
    timeout: float = 5.0,
    connect_timeout: float = 2.0,
    read_timeout: float = 3.0,
    write_timeout: float = 3.0,
    api_key: str = "",
) -> httpx.AsyncClient:
    """Return (or create) a shared httpx.AsyncClient singleton."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    async with _http_client_lock:
        if _http_client is not None and not _http_client.is_closed:
            return _http_client
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=timeout,
                connect=connect_timeout,
                read=read_timeout,
                write=write_timeout,
            ),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30,
            ),
            headers={
                "X-API-Key": api_key,
                "User-Agent": "restaurant-voice-agent/1.0",
            },
        )
        return _http_client


def cleanup_http_client() -> None:
    """Synchronously close the client (for atexit)."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_http_client.aclose())
            else:
                loop.run_until_complete(_http_client.aclose())
        except Exception:
            pass
    _http_client = None


def retry_delay(attempt: int, base_seconds: float) -> float:
    """Exponential backoff with a minimum floor."""
    return max(0.05, base_seconds * (2 ** attempt))


def response_snippet(response: httpx.Response | None, *, limit: int = 300) -> str:
    """Extract a short text snippet from an httpx response for logging."""
    if response is None:
        return ""
    try:
        body = response.text or ""
    except Exception:
        return ""
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit]


def exc_log_fields(exc: Exception) -> str:
    """Format an exception into structured log fields."""
    parts = [f"type={exc.__class__.__name__}", f"repr={exc!r}"]
    if isinstance(exc, httpx.HTTPStatusError):
        req = exc.request
        res = exc.response
        parts.extend(
            [
                f"method={req.method}",
                f"url={req.url}",
                f"status_code={res.status_code}",
            ]
        )
        snippet = response_snippet(res)
        if snippet:
            parts.append(f"body={snippet!r}")
    elif isinstance(exc, httpx.RequestError):
        req = exc.request
        parts.extend([f"method={req.method}", f"url={req.url}"])
    return " | ".join(parts)


def should_retry_backend_error(exc: Exception) -> bool:
    """Return True if the error is retryable (5xx or network error)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(
        exc,
        (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError),
    )
