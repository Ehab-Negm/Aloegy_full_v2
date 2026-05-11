"""Provision a LiveKit-SIP tenant from the command line.

Direct alternative to hitting the backend's
``POST /admin/restaurants/{id}/sip-provision`` endpoint — useful when the
backend isn't running yet (initial bring-up) or when scripting bulk onboards.

Usage:
    python -m agent.ops.provision_tenant \\
        --slug pizza-king \\
        --did +201001234567 \\
        --issabel-ip 41.45.123.45

Env required:
    LIVEKIT_URL          ws[s]://<host>:<port> for the LiveKit server API
    LIVEKIT_API_KEY      same key the agent + backend use
    LIVEKIT_API_SECRET   matching secret

Optional:
    LIVEKIT_AGENT_NAME   agent name registered with the worker (default: aloegy-agent)
    LIVEKIT_SIP_HOST     public DNS for the SIP gateway, prints to the runbook URI
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


def _env(name: str, *, required: bool = True, default: str = "") -> str:
    value = (os.getenv(name) or default).strip()
    if required and not value:
        sys.stderr.write(f"missing required env: {name}\n")
        sys.exit(2)
    return value


async def provision(
    *,
    slug: str,
    did: str,
    issabel_ip: str,
    sip_username: str,
    sip_password: str,
    krisp_enabled: bool,
    agent_name: str,
) -> dict[str, Any]:
    from livekit import api as livekit_api
    from livekit.api import (
        CreateSIPDispatchRuleRequest,
        CreateSIPInboundTrunkRequest,
        RoomConfiguration,
        SIPDispatchRule,
        SIPDispatchRuleIndividual,
        SIPInboundTrunkInfo,
    )

    livekit_url = _env("LIVEKIT_URL")
    livekit_api_key = _env("LIVEKIT_API_KEY")
    livekit_api_secret = _env("LIVEKIT_API_SECRET")

    lk_api = livekit_api.LiveKitAPI(
        url=livekit_url, api_key=livekit_api_key, api_secret=livekit_api_secret,
    )
    try:
        trunk_kwargs: dict[str, Any] = {
            "name": f"{slug}-trunk",
            "numbers": [did],
            "allowed_addresses": [f"{issabel_ip}/32"],
            "krisp_enabled": krisp_enabled,
        }
        if sip_username and sip_password:
            trunk_kwargs["auth_username"] = sip_username
            trunk_kwargs["auth_password"] = sip_password
        trunk = await lk_api.sip.create_sip_inbound_trunk(
            CreateSIPInboundTrunkRequest(trunk=SIPInboundTrunkInfo(**trunk_kwargs))
        )
        trunk_id = getattr(trunk, "sip_trunk_id", "")

        dispatch_metadata = json.dumps(
            {
                "restaurant_id": slug,
                "source": "sip",
                "trunk_id": trunk_id,
                "did": did,
            },
            ensure_ascii=False,
        )
        rule_kwargs: dict[str, Any] = {
            "name": f"{slug}-rule",
            "trunk_ids": [trunk_id] if trunk_id else [],
            "rule": SIPDispatchRule(
                dispatch_rule_individual=SIPDispatchRuleIndividual(room_prefix=f"sip-{slug}-"),
            ),
            "metadata": dispatch_metadata,
        }
        if agent_name:
            try:
                from livekit.api import RoomAgentDispatch
                rule_kwargs["room_config"] = RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name=agent_name, metadata=dispatch_metadata)],
                )
            except ImportError:
                pass

        dispatch = await lk_api.sip.create_sip_dispatch_rule(
            CreateSIPDispatchRuleRequest(**rule_kwargs)
        )
        rule_id = getattr(dispatch, "sip_dispatch_rule_id", "")

        record = {
            "trunk_id": trunk_id,
            "dispatch_rule_id": rule_id,
            "did": did,
            "issabel_ip": issabel_ip,
            "krisp_enabled": krisp_enabled,
            "agent_name": agent_name,
            "provisioned_at": datetime.now(timezone.utc).isoformat(),
        }
        return record
    finally:
        try:
            await lk_api.aclose()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slug", required=True, help="Tenant slug, e.g. pizza-king")
    parser.add_argument("--did", required=True, help="DID in E.164, e.g. +201001234567")
    parser.add_argument("--issabel-ip", required=True, help="Public IPv4 of customer's Issabel")
    parser.add_argument("--sip-username", default="", help="Optional SIP digest auth username")
    parser.add_argument("--sip-password", default="", help="Optional SIP digest auth password")
    parser.add_argument("--no-krisp", action="store_true", help="Disable Krisp noise cancellation")
    parser.add_argument(
        "--agent-name",
        default=os.getenv("LIVEKIT_AGENT_NAME", "aloegy-agent"),
        help="Agent name registered with the worker",
    )
    args = parser.parse_args()

    record = asyncio.run(
        provision(
            slug=args.slug,
            did=args.did,
            issabel_ip=args.issabel_ip,
            sip_username=args.sip_username,
            sip_password=args.sip_password,
            krisp_enabled=not args.no_krisp,
            agent_name=args.agent_name,
        )
    )
    sip_host = os.getenv("LIVEKIT_SIP_HOST", "sip.aloegy.ai").strip()
    record["customer_sip_uri"] = f"sip:{args.did.lstrip('+')}@{sip_host}"
    sys.stdout.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
