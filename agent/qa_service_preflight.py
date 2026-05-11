"""Service reachability preflight for live QA.

Run this from the repo root before collecting the 50-call market QA batch. It
checks the local backend, agent health endpoint, and frontend URL that testers
will use for web calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BACKEND_HEALTH_URL = "http://127.0.0.1:8000/health"
DEFAULT_DEMO_SESSION_URL = "http://127.0.0.1:8000/demo/livekit-session"
DEFAULT_AGENT_HEALTH_URL = "http://127.0.0.1:8082/healthz"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:5173/"


@dataclass
class FetchResult:
    ok: bool
    status_code: int | None = None
    body: str = ""
    error: str = ""


@dataclass
class ServiceReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)


def _fetch(
    url: str,
    *,
    timeout: float,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> FetchResult:
    headers = {"User-Agent": "qa-service-preflight/1.0"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(512_000).decode("utf-8", errors="replace")
            return FetchResult(ok=True, status_code=int(response.status), body=body)
    except HTTPError as exc:
        body = exc.read(64_000).decode("utf-8", errors="replace")
        return FetchResult(ok=False, status_code=int(exc.code), body=body, error=str(exc))
    except (OSError, URLError) as exc:
        return FetchResult(ok=False, error=str(exc))


def _json_body(result: FetchResult) -> dict[str, Any]:
    try:
        value = json.loads(result.body)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def evaluate_services(
    *,
    backend_url: str = DEFAULT_BACKEND_HEALTH_URL,
    demo_session_url: str = DEFAULT_DEMO_SESSION_URL,
    agent_url: str = DEFAULT_AGENT_HEALTH_URL,
    frontend_url: str = DEFAULT_FRONTEND_URL,
    timeout: float = 5.0,
    strict_prod: bool = False,
    check_demo_session: bool = True,
) -> ServiceReport:
    report = ServiceReport(passed=True)

    backend = _fetch(backend_url, timeout=timeout)
    backend_json = _json_body(backend)
    report.checks["backend"] = {
        "url": backend_url,
        "reachable": backend.ok,
        "status_code": backend.status_code,
        "env": backend_json.get("env"),
    }
    if not backend.ok:
        report.errors.append(f"backend health unreachable: {backend_url} ({backend.error or backend.status_code})")
    elif backend_json.get("status") != "ok":
        report.errors.append(f"backend health is not ok: {backend_json}")
    backend_env = backend_json.get("env")
    if backend_env and backend_env != "prod":
        message = f"backend env is {backend_env!r}; production deploy should use 'prod'"
        if strict_prod:
            report.errors.append(message)
        else:
            report.warnings.append(message)

    if check_demo_session:
        demo_body = json.dumps(
            {
                "restaurantId": "demo-restaurant",
                "participantName": "QA preflight",
                "qaPreflight": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        demo = _fetch(
            demo_session_url,
            timeout=timeout,
            method="POST",
            body=demo_body,
            content_type="application/json",
        )
        demo_json = _json_body(demo)
        room_metadata = _json_body(FetchResult(ok=True, body=str(demo_json.get("roomMetadata") or "{}")))
        report.checks["demo_session"] = {
            "url": demo_session_url,
            "reachable": demo.ok,
            "status_code": demo.status_code,
            "ok": bool(demo_json.get("ok")),
            "livekit_url_present": bool(demo_json.get("livekitUrl")),
            "room_name_present": bool(demo_json.get("roomName")),
            "qa_preflight_room_name": str(demo_json.get("roomName") or "").startswith("qa-preflight-"),
            "token_present": bool(demo_json.get("token")),
            "participant_identity_present": bool(demo_json.get("participantIdentity")),
            "room_metadata_present": bool(demo_json.get("roomMetadata")),
            "qa_preflight_metadata": room_metadata.get("source") == "qa_service_preflight",
            "expires_in_seconds": demo_json.get("expiresInSeconds"),
        }
        if not demo.ok:
            report.errors.append(
                f"demo LiveKit session endpoint unreachable: {demo_session_url} ({demo.error or demo.status_code})"
            )
        elif not demo_json.get("ok"):
            report.errors.append("demo LiveKit session endpoint did not return ok=true")
        for field in ("livekitUrl", "roomName", "token", "participantIdentity", "roomMetadata"):
            if not demo_json.get(field):
                report.errors.append(f"demo LiveKit session response missing {field}")
        if demo.ok and not str(demo_json.get("roomName") or "").startswith("qa-preflight-"):
            report.errors.append("demo LiveKit preflight room name was not isolated with qa-preflight- prefix")
        if demo.ok and room_metadata.get("source") != "qa_service_preflight":
            report.errors.append("demo LiveKit session was not tagged as qa_service_preflight")

    agent = _fetch(agent_url, timeout=timeout)
    agent_json = _json_body(agent)
    report.checks["agent"] = {
        "url": agent_url,
        "reachable": agent.ok,
        "status_code": agent.status_code,
        "status": agent_json.get("status"),
        "app_env": agent_json.get("app_env"),
        "livekit_connected": agent_json.get("livekit_connected"),
        "worker_snapshots": agent_json.get("worker_snapshots"),
        "reasons": agent_json.get("reasons", []),
    }
    if not agent.ok:
        report.errors.append(f"agent health unreachable: {agent_url} ({agent.error or agent.status_code})")
    elif agent_json.get("status") != "ok":
        report.errors.append(f"agent health is not ok: {agent_json.get('reasons') or agent_json}")
    if agent_json.get("livekit_connected") is False:
        report.errors.append("agent health reports LiveKit disconnected")
    agent_env = agent_json.get("app_env")
    if strict_prod and agent_env != "prod":
        report.errors.append(f"agent app_env is {agent_env!r}; production deploy should use 'prod'")
    snapshots = agent_json.get("worker_snapshots") or {}
    if isinstance(snapshots, dict) and int(snapshots.get("fresh") or 0) < 1:
        report.errors.append("agent health has no fresh worker snapshot")

    frontend = _fetch(frontend_url, timeout=timeout)
    report.checks["frontend"] = {
        "url": frontend_url,
        "reachable": frontend.ok,
        "status_code": frontend.status_code,
        "html_bytes": len(frontend.body),
    }
    if not frontend.ok:
        report.errors.append(f"frontend unreachable: {frontend_url} ({frontend.error or frontend.status_code})")
    else:
        body_lower = frontend.body[:4096].lower()
        if "<html" not in body_lower or "/src/" not in body_lower:
            report.warnings.append("frontend response did not look like the Vite React app")

    report.passed = not report.errors
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check running services before live QA.")
    parser.add_argument("--backend", default=DEFAULT_BACKEND_HEALTH_URL)
    parser.add_argument("--demo-session", default=DEFAULT_DEMO_SESSION_URL)
    parser.add_argument("--agent", default=DEFAULT_AGENT_HEALTH_URL)
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND_URL)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--strict-prod",
        action="store_true",
        help="Fail if backend or agent health does not report app/env=prod",
    )
    parser.add_argument(
        "--skip-demo-session",
        action="store_true",
        help="Skip POST /demo/livekit-session token-minting check",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = evaluate_services(
        backend_url=args.backend,
        demo_session_url=args.demo_session,
        agent_url=args.agent,
        frontend_url=args.frontend,
        timeout=max(1.0, float(args.timeout)),
        strict_prod=args.strict_prod,
        check_demo_session=not args.skip_demo_session,
    )
    print(json.dumps({
        "passed": report.passed,
        "errors": report.errors,
        "warnings": report.warnings,
        "checks": report.checks,
    }, ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
