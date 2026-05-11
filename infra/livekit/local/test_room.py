"""Smoke-test the agent + livekit pipeline without SIP.

Joins a room with the same metadata the SIP dispatch rule generates
(``restaurant_id=local-test``, ``source=sip``) and waits for the agent
to subscribe to a remote audio track. If we see ``agent-AJ_*`` show up
within 10 seconds, the agent worker + dispatch path are healthy and the
remaining problem is purely SIP/RTP — narrowing the surface considerably.

Usage:
    python test_room.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

from livekit import api, rtc

LK_URL = "ws://localhost:7880"
API_KEY = "devkey"
API_SECRET = "1c4edda63807bc8dd223268255ada00eb474143e9832009aa786dac72bbf644f"


async def main() -> int:
    room_name = f"sip-local-test-pytest_{uuid.uuid4().hex[:8]}"
    metadata = json.dumps(
        {"restaurant_id": "local-test", "source": "sip", "trunk_id": "ST_test"},
        ensure_ascii=False,
    )

    # Create the room with metadata so the agent's restaurant resolver matches.
    lk = api.LiveKitAPI(url=LK_URL, api_key=API_KEY, api_secret=API_SECRET)
    try:
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                metadata=metadata,
                agents=[api.RoomAgentDispatch(agent_name="aloegy-agent", metadata=metadata)],
            )
        )
        print(f"created room {room_name}")
    finally:
        await lk.aclose()

    # Mint a participant token so we can join.
    token = (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity("pytest_caller")
        .with_name("pytest")
        .with_grants(api.VideoGrants(room=room_name, room_join=True, can_publish=True, can_subscribe=True))
        .to_jwt()
    )

    room = rtc.Room()
    saw_agent = asyncio.Event()
    agent_track = asyncio.Event()

    @room.on("participant_connected")
    def _on_pc(p: rtc.RemoteParticipant) -> None:  # pragma: no cover
        print(f"participant_connected: identity={p.identity} name={p.name}")
        if p.identity.startswith("agent-"):
            saw_agent.set()

    @room.on("track_subscribed")
    def _on_track(track, pub, p):  # pragma: no cover
        print(f"track_subscribed: from={p.identity} kind={track.kind}")
        if p.identity.startswith("agent-"):
            agent_track.set()

    print(f"joining {room_name} as pytest_caller...")
    await room.connect(LK_URL, token)
    print(f"joined.  waiting up to 20s for agent to appear...")

    try:
        await asyncio.wait_for(saw_agent.wait(), timeout=20)
        print("[OK] agent connected to the room")
    except asyncio.TimeoutError:
        print("[FAIL] agent never connected in 20s — auto-dispatch is broken or worker isn't picking up new rooms")
        await room.disconnect()
        return 1

    try:
        await asyncio.wait_for(agent_track.wait(), timeout=15)
        print("[OK] agent published an audio track")
    except asyncio.TimeoutError:
        print("[WARN]  agent connected but never published audio — check Gemini Live API key / model")

    # Linger a couple seconds in case audio arrives slightly later.
    await asyncio.sleep(2)
    print("disconnecting.")
    await room.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
