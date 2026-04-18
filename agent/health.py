"""Lightweight health endpoint for the agent parent process."""
from __future__ import annotations

import atexit
import json
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


logger = logging.getLogger("restaurant.agent")


class _HealthHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@dataclass
class HealthServerHandle:
    host: str
    port: int
    _server: _HealthHTTPServer
    _thread: threading.Thread

    def close(self) -> None:
        try:
            self._server.shutdown()
        except Exception:
            logger.exception("health server shutdown failed")
        try:
            self._server.server_close()
        except Exception:
            logger.exception("health server close failed")


def start_health_server(
    *,
    host: str,
    port: int,
    report_builder: Callable[[], tuple[int, dict[str, Any]]],
) -> HealthServerHandle | None:
    """Start a small `/healthz` HTTP endpoint in a background thread."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/healthz":
                self.send_error(404)
                return

            try:
                status_code, payload = report_builder()
            except Exception:
                logger.exception("health report builder failed")
                status_code = 500
                payload = {"status": "unhealthy", "error": "health_report_failed"}

            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            logger.debug("health http | " + format, *args)

    try:
        httpd = _HealthHTTPServer((host, port), Handler)
    except OSError as exc:
        logger.warning(
            "health server start skipped | host=%s | port=%s | error=%s",
            host,
            port,
            exc,
        )
        return None

    thread = threading.Thread(
        target=httpd.serve_forever,
        name=f"agent-health-{httpd.server_port}",
        daemon=True,
    )
    thread.start()

    handle = HealthServerHandle(
        host=host,
        port=httpd.server_port,
        _server=httpd,
        _thread=thread,
    )
    atexit.register(handle.close)
    logger.info("agent health server listening on %s:%s/healthz", host, httpd.server_port)
    return handle
