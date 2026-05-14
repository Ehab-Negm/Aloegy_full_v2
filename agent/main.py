"""Entrypoint — LiveKit AgentServer + session setup."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing as mp
import os
import re
import time
import uuid
import json as _json
from datetime import datetime, timezone
from typing import Any

from livekit.agents import AgentServer, JobContext, cli
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import AgentSession
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.plugins import noise_cancellation

# DTLN plugin self-registers on import; LiveKit-agents only allows plugin
# registration on the main thread, so we import here (at module load time
# on the worker's main thread) rather than lazily inside the entrypoint
# (which runs on a job thread).
try:
    from livekit.plugins import dtln as _dtln  # type: ignore
except ImportError:  # pragma: no cover — optional dep
    _dtln = None  # type: ignore[assignment]

from backend.config import RestaurantConfig
from health import HealthServerHandle, start_health_server
from state.user_data import UserData
from state.worker_context import WorkerContext

import agent as _agent
from observability.call_metrics import finalize_call as _finalize_call_metrics
from observability.call_metrics import get_or_create as _get_call_metrics
from restaurant_agent import RestaurantAgent

logger = logging.getLogger("restaurant.agent")

AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "aloegy-agent").strip() or "aloegy-agent"

server = AgentServer(
    num_idle_processes=_agent.AGENT_IDLE_PROCESSES,
    setup_fnc=_agent._setup_worker_process,
)

MAX_CALL_DURATION = _agent._get_env_int("MAX_CALL_DURATION", 600, min_value=30)
TARGET_E2E_FIRST_AUDIO_MS = _agent._get_env_float(
    "TARGET_E2E_FIRST_AUDIO_MS", 600.0, min_value=100.0
)
SESSION_NOISE_CANCELLATION = os.getenv("SESSION_NOISE_CANCELLATION", "bvc_telephony").strip().lower()
AGENT_HEALTH_HOST = os.getenv("AGENT_HEALTH_HOST", "0.0.0.0")
AGENT_HEALTH_PORT = _agent._get_env_int("AGENT_HEALTH_PORT", 8082, min_value=0)
_HEALTH_SERVER_HANDLE: HealthServerHandle | None = None


def _build_health_report() -> tuple[int, dict[str, Any]]:
    return _agent.build_agent_health_report(
        server_connection_failed=bool(getattr(server, "_connection_failed", False)),
        active_jobs=len(getattr(server, "active_jobs", [])),
    )


def _start_parent_health_server() -> None:
    global _HEALTH_SERVER_HANDLE
    if _HEALTH_SERVER_HANDLE is not None:
        return
    if mp.parent_process() is not None:
        return
    _HEALTH_SERVER_HANDLE = start_health_server(
        host=AGENT_HEALTH_HOST,
        port=AGENT_HEALTH_PORT,
        report_builder=_build_health_report,
    )


def _tool_output_indicates_failure(output_text: str) -> bool:
    """Heuristic: did this tool return a user-facing failure?

    Tool failures are flagged as success=false in telemetry. The flag is
    intentionally narrow — only patterns that *only* appear in failure
    branches of the tool surface count. Substring matching against single
    Arabic words caused false positives (e.g. set_intent's success message
    contains the word "ناقص" inside a sentence telling the agent to find
    what's missing — that's healthy guidance, not a failure).
    """
    normalized = (output_text or "").strip().lower()
    if not normalized:
        return False

    # Markers that are unambiguous: explicit error prefixes, English error
    # words, or distinctive multi-word Arabic phrases used only in the
    # failure branches of restaurant_tools.py.
    unambiguous_markers = (
        "critical:",
        "خارج نطاق",
        "مش متاح",
        "أقل من الحد",
        "اقل من الحد",
        "cannot",
        "unavailable",
        "error",
    )
    if any(marker in normalized for marker in unambiguous_markers):
        return True

    # Words like "ناقص"/"فاضي"/"فشل" only count as failure signals when
    # they appear at the START of the message (the tools always lead with
    # them in failure branches: "ناقص الطلب", "العنوان فاضي", "فشل …").
    leading_window = normalized[:60]
    leading_failure_words = ("ناقص ", "فشل ", "العنوان فاضي", "الطلب فاضي", "الاسم فاضي")
    return any(word in leading_window for word in leading_failure_words)


def _extract_sip_identity(ctx: JobContext) -> dict[str, str]:
    """Pull SIP attributes from the room's remote participants, if any.

    LiveKit-SIP injects participant attributes prefixed with ``sip.`` when a
    room is created from a SIP INVITE (e.g. ``sip.callID``, ``sip.phoneNumber``,
    ``sip.trunkID``). For browser-widget calls there are no SIP participants,
    so the dict comes back empty.

    Returns the raw attribute mapping. Caller decides what to use.
    """
    return _scan_sip_attrs(ctx)


def _scan_sip_attrs(ctx: JobContext) -> dict[str, str]:
    """One-shot scan over remote participants for sip.* attributes.

    Always logs what it sees so we can debug missing-caller-phone reports
    without needing a Wireshark capture.
    """
    sip_meta: dict[str, str] = {}
    remote_participants = getattr(ctx.room, "remote_participants", {}) or {}
    iter_values = (
        remote_participants.values()
        if hasattr(remote_participants, "values")
        else remote_participants
    )
    participant_dumps: list[str] = []
    for participant in iter_values:
        identity = getattr(participant, "identity", "") or ""
        kind = getattr(participant, "kind", "")
        attrs = getattr(participant, "attributes", None) or {}
        attrs_dict = {str(k): str(v) for k, v in attrs.items()}
        participant_dumps.append(
            f"identity={identity} kind={kind} attrs={attrs_dict or '<empty>'}"
        )
        if not sip_meta:
            sip_meta = {k: v for k, v in attrs_dict.items() if k.startswith("sip.")}
    if participant_dumps:
        logger.debug("scan_sip_attrs | participants=%s", participant_dumps)
    return sip_meta


async def _wait_for_sip_attrs(
    ctx: JobContext,
    *,
    timeout: float = 0.4,
    poll_interval: float = 0.05,
) -> dict[str, str]:
    """Poll remote participants until SIP attributes show up.

    Participant attributes propagate slightly after the participant itself
    joins the room — LiveKit publishes them in a separate sync message. If
    we read at job-start before the sync lands, ``sip.*`` is empty.

    The running LiveKit-SIP build does NOT publish ``sip.*`` attributes the
    way the agent expects (every SIP call hits the warning fallback). The
    phone number is recoverable from ``participant.identity`` / room-name
    suffix at zero cost, so the budget here is kept tight (400ms) instead
    of the original 3s wait that was the largest single contributor to
    pre-greeting latency on phone calls.
    """
    deadline = time.monotonic() + timeout
    last_dump: dict[str, str] = {}
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        attrs = _scan_sip_attrs(ctx)
        if attrs:
            logger.info(
                "sip attrs ready after %d attempt(s) | keys=%s",
                attempts, sorted(attrs.keys()),
            )
            return attrs
        last_dump = attrs
        await asyncio.sleep(poll_interval)
    logger.warning(
        "sip attrs never arrived after %.1fs (%d attempts). last=%s",
        timeout, attempts, last_dump or "<no participants>",
    )
    return last_dump


def _parse_metadata(raw: object) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        logger.warning("could not parse metadata: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_call_identity(ctx: JobContext, room_metadata: dict[str, object]) -> tuple[str, str, dict[str, str]]:
    """Decide ``call_id`` and ``call_source`` for this room.

    For SIP-originated rooms we prefer the Asterisk Call-ID so logs cross-
    reference cleanly with the customer's PBX. For web calls we fall back
    to a random UUID. If the dispatch rule metadata flags ``source=sip``
    but no SIP attributes appeared yet, the call still counts as SIP and
    we just generate a UUID (logged so we can chase the missing inject).
    """
    sip_meta = _extract_sip_identity(ctx)
    metadata_source = str(room_metadata.get("source") or "").strip().lower()
    # Robust SIP detection: trust the room name + participant identity even
    # when metadata / attributes haven't synced yet. Dispatch rules prefix
    # rooms with "sip-…" and LiveKit-SIP names participants "sip_…".
    room_name = str(getattr(ctx.room, "name", "") or "")
    is_sip_by_room = room_name.lower().startswith("sip-") or "sip-" in room_name.lower()
    is_sip_by_participant = False
    try:
        for p in (getattr(ctx.room, "remote_participants", {}) or {}).values():
            ident = str(getattr(p, "identity", "") or "")
            if ident.lower().startswith("sip_") or "sip" in ident.lower()[:6]:
                is_sip_by_participant = True
                break
    except Exception:
        pass

    if sip_meta:
        sip_call_id = sip_meta.get("sip.callID") or sip_meta.get("sip.callId") or ""
        if sip_call_id:
            return sip_call_id[:32], "sip", sip_meta
        return str(uuid.uuid4())[:16], "sip", sip_meta
    if metadata_source == "sip" or is_sip_by_room or is_sip_by_participant:
        return str(uuid.uuid4())[:16], "sip", {}
    return str(uuid.uuid4())[:16], "web", {}


_FROM_URI_RE = re.compile(r"<sip:([^@>;]+)", re.IGNORECASE)
_DIGITS_RE = re.compile(r"[+]?\d{7,15}")


def _extract_phone_from_identity(identity: str) -> str:
    """Pull a phone-number-shaped substring from a SIP participant identity.

    LiveKit-SIP names inbound participants as ``sip_<phone>``,
    ``sip_+201...``, or — with chan_dongle — ``sip_anonymous``. We accept
    anything that looks like a 7-15 digit run (optionally leading +).
    """
    if not identity:
        return ""
    cleaned = identity.strip()
    if cleaned.lower().startswith("sip_"):
        cleaned = cleaned[4:]
    if cleaned.lower() in {"anonymous", "unknown", "restricted"}:
        return ""
    match = _DIGITS_RE.search(cleaned)
    return match.group(0) if match else ""


def _extract_phone_from_room_name(room_name: str) -> str:
    """Pull a phone-number-shaped substring from a SIP room name.

    Dispatch rules produce names like ``sip-local-test-_01558950484`` —
    the trailing token is the caller. Useful as a final fallback when the
    participant attribute bag is still empty.
    """
    if not room_name:
        return ""
    # Walk the name from right to left looking for a digit run.
    tokens = re.split(r"[^\d+]+", room_name)
    for tok in reversed(tokens):
        if tok and len(re.sub(r"\D", "", tok)) >= 7:
            return tok
    return ""


def _pick_caller_phone(sip_meta: dict[str, str]) -> str:
    """Pick the best-available caller phone from a SIP attribute bag.

    Confirmed in livekit-sip binary strings: the only first-class key is
    ``sip.phoneNumber`` and the raw From header is exposed as ``sip.h.From``
    (the ``sip.h.*`` prefix is how livekit-sip surfaces arbitrary INVITE
    headers). Older / forked variants and Telnyx integrations have used
    ``sip.fromUser`` / ``sip.from``, so we try those too.

    Anonymous / placeholder values are treated as "no number" so chan_dongle
    callers whose CID was suppressed don't silently end up as ``anonymous``
    in the order payload.
    """
    candidates = (
        "sip.phoneNumber",
        "sip.phone_number",
        "sip.fromUser",
        "sip.from_user",
        "sip.from",
    )
    for key in candidates:
        value = (sip_meta.get(key) or "").strip()
        if value.startswith("sip:"):
            value = value[4:].split("@", 1)[0].strip()
        if value and value.lower() not in {"anonymous", "unknown", "restricted"}:
            return value

    # Fallback: parse the raw From header. Example value from chan_dongle:
    #   '"dongle0" <sip:01558950484@192.168.8.16>;tag=as7e21d1c8'
    for key in ("sip.h.From", "sip.h.from"):
        raw = (sip_meta.get(key) or "").strip()
        if not raw:
            continue
        match = _FROM_URI_RE.search(raw)
        if not match:
            continue
        user = match.group(1).strip()
        if user and user.lower() not in {"anonymous", "unknown", "restricted"}:
            return user
    return ""


def _wire_late_sip_attrs(
    ctx: JobContext, userdata: "UserData", call_id: str
) -> None:
    """Listen for participant attribute changes after the agent is connected.

    Sometimes the SIP participant's attributes haven't synced when the agent
    spins up. LiveKit fires ``participant_attributes_changed`` later; we
    backfill ``caller_phone`` / ``customer_phone`` at that moment so the order
    payload still gets the right number even if we missed it at job start.
    """
    room = getattr(ctx, "room", None)
    if room is None:
        return

    def _handler(changed: dict, participant: Any) -> None:
        try:
            attrs = getattr(participant, "attributes", None) or {}
            sip_only = {str(k): str(v) for k, v in attrs.items() if str(k).startswith("sip.")}
            if not sip_only:
                return
            new_phone = _pick_caller_phone(sip_only)
            if not new_phone or userdata.caller_phone == new_phone:
                return
            userdata.caller_phone = new_phone
            if not userdata.customer_phone:
                userdata.customer_phone = new_phone
                userdata.confirmed_facts.phone_confirmed = True
            logger.info(
                "call=%s | late sip attribute backfill | caller=%s | from=%s",
                call_id, new_phone, sorted(sip_only.keys()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("call=%s | late sip attr handler failed: %s", call_id, exc)

    try:
        room.on("participant_attributes_changed", _handler)
    except Exception as exc:  # noqa: BLE001
        logger.debug("call=%s | couldn't wire late sip attr listener: %s", call_id, exc)


def _is_self_hosted_livekit() -> bool:
    """Detect a self-hosted LiveKit server vs LiveKit Cloud.

    Krisp BVC (audio_filter) is a LiveKit Cloud feature. Enabling it
    against a self-hosted server produces the noisy warning
    ``"audio filter cannot be enabled: LiveKit Cloud is required"`` on
    every call. Auto-detect so the user doesn't have to remember an env
    flag when they switch between local docker and Cloud.
    """
    url = (os.getenv("LIVEKIT_URL") or "").strip().lower()
    if not url:
        return False
    if url.startswith("ws://localhost") or url.startswith("ws://127.0.0.1"):
        return True
    if url.startswith("ws://"):  # any plain-ws endpoint is self-hosted
        return True
    return ".livekit.cloud" not in url


def _build_dtln_filter() -> Any | None:
    """Build a DTLN noise-suppression filter for inbound audio.

    DTLN is MIT-licensed, CPU-only, native 16 kHz (perfect for SIP), and
    adds <1ms of latency. The ``DTLN_STRENGTH`` env knob (0.0-1.0, default
    0.4) mixes denoised + original audio so Arabic consonants like ع/ح/ش
    don't get clipped by an over-aggressive mask.

    The actual module import happens at the top of main.py (on the worker's
    main thread). If the package isn't installed, ``_dtln`` is None here and
    we just return None — the caller falls back to no-NC and logs a warning.
    """
    if _dtln is None:
        logger.warning(
            "DTLN plugin not installed — install livekit-plugins-dtln to "
            "enable self-hosted noise cancellation"
        )
        return None
    strength_raw = os.getenv("DTLN_STRENGTH", "0.4").strip()
    try:
        strength = max(0.0, min(1.0, float(strength_raw)))
    except ValueError:
        strength = 0.4
    try:
        return _dtln.noise_suppression(strength=strength)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to build DTLN filter: %s", exc)
        return None


def _build_room_input_options(*, realtime_mode: bool = False) -> RoomInputOptions:
    """Configure inbound audio processing for the agent.

    Three NC paths are supported:
      * ``SESSION_NOISE_CANCELLATION=dtln`` (recommended for self-hosted) —
        runs DTLN in-process at <1ms latency, fully offline, MIT-licensed.
        Works in realtime mode too: pre-cleaning the SIP leg before Gemini
        Live still helps on noisy phone audio, even though Gemini does its
        own server-side NC.
      * ``SESSION_NOISE_CANCELLATION=bvc_telephony`` / ``bvc`` / ``nc`` —
        LiveKit Cloud's Krisp models. Only works against LiveKit Cloud
        servers; on self-hosted the SDK silently drops them.
      * ``SESSION_NOISE_CANCELLATION=off`` — bypass NC entirely.
    """
    mode = (SESSION_NOISE_CANCELLATION or "").strip().lower()

    # DTLN — the right answer for self-hosted + SIP. Works in both
    # realtime and classical modes since it runs at the LiveKit edge.
    if mode in {"dtln", "selfhosted", "self_hosted", "self-hosted"}:
        filt = _build_dtln_filter()
        if filt is not None:
            logger.info("noise cancellation enabled | mode=dtln")
            return RoomInputOptions(noise_cancellation=filt)
        logger.warning("DTLN requested but unavailable — falling back to no-NC")
        return RoomInputOptions()

    if mode in {"", "0", "false", "off", "none", "disabled"}:
        logger.info("noise cancellation disabled | mode=%s", mode or "off")
        return RoomInputOptions()

    # Cloud-only Krisp paths below. Skip when:
    #   * realtime model owns NC (Gemini Live applies it server-side), OR
    #   * the LiveKit server is self-hosted (Cloud audio filter unavailable —
    #     the SDK only logs a warning and drops the filter anyway).
    if realtime_mode and mode not in {"force", "always"}:
        logger.info("noise cancellation skipped (realtime model owns NC)")
        return RoomInputOptions()
    if _is_self_hosted_livekit() and mode not in {"force", "always"}:
        logger.info(
            "noise cancellation skipped (self-hosted LiveKit: cloud audio filter unavailable). "
            "Set SESSION_NOISE_CANCELLATION=dtln to enable on-prem NC."
        )
        return RoomInputOptions()

    factories = {
        "bvc_telephony": noise_cancellation.BVCTelephony,
        "bvctelephony": noise_cancellation.BVCTelephony,
        "telephony": noise_cancellation.BVCTelephony,
        "bvc": noise_cancellation.BVC,
        "voice": noise_cancellation.BVC,
        "voice_isolation": noise_cancellation.BVC,
        "nc": noise_cancellation.NC,
        "noise": noise_cancellation.NC,
        "noise_suppression": noise_cancellation.NC,
    }
    factory = factories.get(mode)
    if factory is None:
        logger.warning(
            "unknown SESSION_NOISE_CANCELLATION=%s; falling back to bvc_telephony",
            mode,
        )
        mode = "bvc_telephony"
        factory = noise_cancellation.BVCTelephony
    logger.info("noise cancellation enabled | mode=%s", mode)
    return RoomInputOptions(noise_cancellation=factory())


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    call_started_at_utc = datetime.now(timezone.utc)
    proc_context = ctx.proc.userdata.get("worker_context")
    if isinstance(proc_context, WorkerContext) and _agent._WORKER_CONTEXT is None:
        _agent._WORKER_CONTEXT = proc_context

    job_metadata = _parse_metadata(getattr(getattr(ctx, "job", None), "metadata", None))
    room_metadata = _parse_metadata(getattr(ctx.room, "metadata", None))
    merged_metadata = {**room_metadata, **job_metadata}
    restaurant_id = str(merged_metadata.get("restaurant_id", ""))

    # Resolve identity AFTER metadata is parsed so SIP rooms can use the
    # Asterisk Call-ID as call_id (cleaner cross-system logs than a random
    # UUID). Falls back to UUID for browser-widget calls.
    call_id, call_source, sip_meta = _resolve_call_identity(ctx, merged_metadata)

    # If this is a SIP-dispatched room but attributes haven't synced yet,
    # poll for a few hundred ms. Without this the first attribute read often
    # lands while LiveKit is still pushing the sip.* bag to us, and the
    # caller's phone gets dropped on the floor.
    metadata_source_early = str(merged_metadata.get("source") or "").strip().lower()
    if call_source == "sip" or metadata_source_early == "sip":
        if not sip_meta or not any(
            sip_meta.get(k)
            for k in ("sip.phoneNumber", "sip.phone_number", "sip.fromUser",
                      "sip.from_user", "sip.from")
        ):
            sip_meta = await _wait_for_sip_attrs(ctx)
            # Refresh call_source/call_id from late-arriving attrs.
            if sip_meta:
                call_source = "sip"
                late_call_id = sip_meta.get("sip.callID") or sip_meta.get("sip.callId") or ""
                if late_call_id:
                    call_id = late_call_id[:32]

    caller_phone = _pick_caller_phone(sip_meta)
    # Fallback chain: if SIP attributes didn't carry the phone, the dispatch
    # rule will have folded the caller's number into the participant identity
    # (e.g. ``sip_01558950484``) and the room name suffix
    # (``sip-…-_01558950484``). Either gets us back what we need.
    if not caller_phone and call_source == "sip":
        try:
            for p in (getattr(ctx.room, "remote_participants", {}) or {}).values():
                ident = str(getattr(p, "identity", "") or "")
                from_ident = _extract_phone_from_identity(ident)
                if from_ident:
                    caller_phone = from_ident
                    logger.info(
                        "caller_phone recovered from participant identity '%s' -> %s",
                        ident, caller_phone,
                    )
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug("identity scan failed: %s", exc)
    if not caller_phone and call_source == "sip":
        rname = str(getattr(ctx.room, "name", "") or "")
        from_room = _extract_phone_from_room_name(rname)
        if from_room:
            caller_phone = from_room
            logger.info(
                "caller_phone recovered from room name '%s' -> %s",
                rname, caller_phone,
            )
    sip_trunk_id = sip_meta.get("sip.trunkID", "") or sip_meta.get("sip.trunkId", "")
    sip_call_id = sip_meta.get("sip.callID", "") or sip_meta.get("sip.callId", "")
    # Log every sip.* attribute once so we can see exactly what's available
    # when a call comes in (helps debug provider quirks like chan_dongle
    # sending a synthetic From user).
    if sip_meta:
        logger.info(
            "call=%s | sip attributes | %s",
            call_id,
            " | ".join(f"{k}={v!r}" for k, v in sorted(sip_meta.items())),
        )
    elif call_source == "sip":
        logger.warning(
            "call=%s | source=sip but no sip.* attributes found after wait — "
            "caller phone will be empty; check LiveKit-SIP version",
            call_id,
        )

    room_name = str(getattr(ctx.room, "name", "") or "")
    metadata_source = str(merged_metadata.get("source") or "").strip().lower()
    if (
        room_name.startswith("qa-preflight-")
        or metadata_source == "qa_service_preflight"
        or bool(merged_metadata.get("qa_preflight"))
    ):
        logger.info("call=%s | qa service preflight room ignored | room=%s", call_id, ctx.room.name)
        return

    logger.info(
        "call=%s | started | room=%s | source=%s | caller=%s | trunk=%s",
        call_id,
        room_name,
        call_source,
        caller_phone or "-",
        sip_trunk_id or "-",
    )
    _agent._emit_event(
        "call.start",
        call_id=call_id,
        room=room_name,
        source=call_source,
        caller_phone=caller_phone,
        trunk_id=sip_trunk_id,
        restaurant_id=restaurant_id,
    )
    _get_call_metrics(call_id)

    if not await _agent._acquire_session_slot(call_id):
        logger.warning("call=%s | rejected at capacity", call_id)
        return

    # Fire-and-forget warmups BEFORE awaiting config so handshakes overlap
    # with config fetch — the first user turn doesn't pay the cold-connection
    # tax (TLS+HTTP/2 setup, first-token latency, TTS WS handshake). Pure
    # realtime mode skips both LLM and TTS warmups: the realtime model warms
    # itself on connect, and there is no TTS attached.
    realtime_mode = _agent.SESSION_REALTIME is not None
    backend_warmup_task = asyncio.create_task(_agent.warmup_backend(), name=f"backend_warmup_{call_id}")
    if realtime_mode:
        llm_warmup_task: asyncio.Task | None = None
        tts_warmup_task: asyncio.Task | None = None
    else:
        llm_warmup_task = asyncio.create_task(_agent.warmup_llm(), name=f"llm_warmup_{call_id}")
        tts_warmup_task = asyncio.create_task(_agent.warmup_tts(), name=f"tts_warmup_{call_id}")

    cfg = await _agent.fetch_config(call_id, restaurant_id=restaurant_id)
    userdata = UserData(call_id=call_id, restaurant=cfg)
    userdata.worker_context = _agent.worker_context()
    userdata.call_source = call_source
    userdata.caller_phone = caller_phone
    userdata.sip_trunk_id = sip_trunk_id
    userdata.sip_call_id = sip_call_id
    userdata.call_started_at = time.time()
    # Forward room-metadata restaurant_id to backend POSTs. Skip the agent's
    # ``__default__`` sentinel; only real public_ids ("demo-restaurant",
    # "pizza-king", …) get pinned via X-Restaurant-ID. SIP rooms without
    # tenant routing fall through to the backend's default tenant.
    _rid_meta = (restaurant_id or "").strip()
    if _rid_meta and _rid_meta != "__default__":
        userdata.restaurant_public_id = _rid_meta
    # SIP calls already carry the caller's number in the From header — use it
    # as the customer_phone so the agent doesn't ask the customer to repeat
    # their own number. Mark the slot confirmed so the persona's "ask name +
    # phone" flow skips the phone question entirely. Web calls leave both
    # fields empty as before.
    if call_source == "sip" and caller_phone:
        userdata.customer_phone = caller_phone
        userdata.confirmed_facts.phone_confirmed = True
        logger.info(
            "call=%s | seeded customer_phone from SIP caller=%s",
            call_id, caller_phone,
        )
    # If attributes were still in flight when we read them, the listener
    # backfills caller_phone the moment LiveKit pushes them. Safe on web
    # calls too — the handler exits early when no sip.* keys arrive.
    if call_source == "sip":
        _wire_late_sip_attrs(ctx, userdata, call_id)
    await _agent._ensure_backend_queue_worker_started()
    await _agent._ensure_config_refresh_started()
    # Skip the standalone STT / context-term computation entirely in realtime
    # Skip the standalone STT / context-term computation entirely in realtime
    # mode — the unified Gemini Live model owns transcription internally and
    # does not consume keyterms supplied to a separate Soniox session.
    if realtime_mode:
        session_stt = None
        stt_context_terms: list[str] = []
    else:
        session_stt = _agent._build_session_stt(cfg, client_reference_id=call_id)
        stt_context_terms = _agent._stt_context_terms_for_config(cfg)
    # Derive config_available from the actual cfg this call is using, not from
    # shared worker-level runtime_health. The latter is process-wide and can be
    # briefly flipped to False by a concurrent call that fell back to degraded,
    # producing the confusing "config_available=False | config_source=backend"
    # readout even when this call fetched a valid backend config.
    config_available_for_call = cfg.config_source != "degraded_fallback"
    logger.info(
        "call=%s | startup readiness | deps_ready=%s | config_available=%s | write_available=%s | config_source=%s | degraded=%s | stt_provider=%s | stt_context_terms=%d | preemptive=%s",
        call_id,
        _agent.session_dependencies_ready(),
        config_available_for_call,
        _agent.backend_write_available(userdata.write_health),
        cfg.config_source,
        cfg.degraded_mode,
        _agent.SESSION_STT_PROVIDER,
        len(stt_context_terms),
        _agent.SESSION_PREEMPTIVE_GENERATION,
    )
    logger.info(
        "call=%s | latency profile | target=%.0fms | endpointing=%.2f-%.2fs | stt_endpoint=%dms | aec_warmup=%.2fs | tts_warmup_join=%.2fs | llm_model=%s | llm_max_tokens=%d",
        call_id,
        TARGET_E2E_FIRST_AUDIO_MS,
        _agent.MIN_ENDPOINTING_DELAY_SECONDS,
        _agent.MAX_ENDPOINTING_DELAY_SECONDS,
        _agent.SESSION_STT_MAX_ENDPOINT_DELAY_MS,
        _agent.AEC_WARMUP_DURATION_SECONDS,
        _agent.TTS_WARMUP_JOIN_TIMEOUT_SECONDS,
        _agent.SESSION_LLM_MODEL,
        _agent.SESSION_LLM_MAX_COMPLETION_TOKENS,
    )
    _agent._emit_event(
        "latency.config",
        call_id=call_id,
        target_ms=round(TARGET_E2E_FIRST_AUDIO_MS, 3),
        min_endpointing_ms=round(_agent.MIN_ENDPOINTING_DELAY_SECONDS * 1000, 3),
        max_endpointing_ms=round(_agent.MAX_ENDPOINTING_DELAY_SECONDS * 1000, 3),
        stt_max_endpoint_delay_ms=_agent.SESSION_STT_MAX_ENDPOINT_DELAY_MS,
        aec_warmup_ms=round(_agent.AEC_WARMUP_DURATION_SECONDS * 1000, 3),
        tts_warmup_join_ms=round(_agent.TTS_WARMUP_JOIN_TIMEOUT_SECONDS * 1000, 3),
        llm_model=_agent.SESSION_LLM_MODEL,
        llm_max_tokens=_agent.SESSION_LLM_MAX_COMPLETION_TOKENS,
    )

    # Single-agent design: one LLM-driven agent handles every flow via tools.
    # No 5-class handoff, no per-flow openings, no flow-switch interceptors.
    main_agent = RestaurantAgent(cfg)
    userdata.agents = {"main": main_agent}

    # Avoid making the first greeting pay the TTS websocket handshake. Config
    # fetch normally overlaps this; if it was too fast, wait a small bounded
    # amount. LLM/backend warmups stay best-effort and never block call start.
    # Skip entirely in realtime mode — the unified model handles its own warmup.
    if (
        tts_warmup_task is not None
        and _agent.TTS_WARMUP_JOIN_TIMEOUT_SECONDS > 0
        and not tts_warmup_task.done()
    ):
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(
                asyncio.shield(tts_warmup_task),
                timeout=_agent.TTS_WARMUP_JOIN_TIMEOUT_SECONDS,
            )

    # Pure realtime: the Gemini Live model owns the entire audio pipeline.
    # No standalone TTS is attached because mixing a side TTS in the same
    # session causes audio echo (model hears its own TTS opening as user
    # speech and reacts to it). The trade-off is that the model is
    # reactive-only — it cannot speak proactively. The customer hears
    # silence until they speak first, then the model greets and continues.
    if _agent.SESSION_REALTIME is not None:
        logger.info(
            "call=%s | session mode=realtime | model=%s | voice=%s",
            call_id,
            _agent.SESSION_REALTIME_MODEL,
            _agent.SESSION_REALTIME_VOICE,
        )
        session = AgentSession[UserData](
            userdata       = userdata,
            llm            = _agent.SESSION_REALTIME,
            vad            = _agent.SESSION_VAD,
            turn_handling={
                "turn_detection": None,
                "endpointing": {
                    "mode": "fixed",
                    "min_delay": _agent.MIN_ENDPOINTING_DELAY_SECONDS,
                    "max_delay": _agent.MAX_ENDPOINTING_DELAY_SECONDS,
                },
                "interruption": {
                    "enabled": True,
                    "mode": "vad",
                    "min_duration": _agent.MIN_INTERRUPTION_DURATION_SECONDS,
                    "min_words": 0,
                    "false_interruption_timeout": _agent.FALSE_INTERRUPTION_TIMEOUT_SECONDS,
                    "resume_false_interruption": True,
                    "discard_audio_if_uninterruptible": True,
                },
            },
            user_away_timeout=_agent.USER_AWAY_TIMEOUT_SECONDS,
            aec_warmup_duration=_agent.AEC_WARMUP_DURATION_SECONDS,
            max_tool_steps = _agent.MAX_TOOL_STEPS,
        )
    else:
        session = AgentSession[UserData](
            userdata       = userdata,
            stt            = session_stt,
            llm            = _agent.SESSION_LLM,
            tts            = _agent.SESSION_TTS,
            vad            = _agent.SESSION_VAD,
            turn_handling={
                # Multilingual turn detection does not support Arabic reliably here.
                # Keep endpointing and interruption local to VAD so LiveKit does not
                # call the adaptive cloud service that returns region error 2008.
                "turn_detection": None,
                "endpointing": {
                    "mode": "fixed",
                    "min_delay": _agent.MIN_ENDPOINTING_DELAY_SECONDS,
                    "max_delay": _agent.MAX_ENDPOINTING_DELAY_SECONDS,
                },
                "interruption": {
                    "enabled": True,
                    "mode": "vad",
                    "min_duration": _agent.MIN_INTERRUPTION_DURATION_SECONDS,
                    "min_words": 0,
                    "false_interruption_timeout": _agent.FALSE_INTERRUPTION_TIMEOUT_SECONDS,
                    "resume_false_interruption": True,
                    "discard_audio_if_uninterruptible": True,
                },
            },
            user_away_timeout=_agent.USER_AWAY_TIMEOUT_SECONDS,
            preemptive_generation=_agent.SESSION_PREEMPTIVE_GENERATION,
            aec_warmup_duration=_agent.AEC_WARMUP_DURATION_SECONDS,
            max_tool_steps = _agent.MAX_TOOL_STEPS,
        )

    # Per-turn aggregation: latest stage metrics keyed by stage name. When a TTS
    # metric arrives (the last stage in the user-to-audio pipeline), we emit a
    # single e2e latency summary so regressions are obvious in production logs.
    turn_metrics: dict[str, float] = {}

    @session.on("metrics_collected")
    def _on_metrics(event):
        m = event.metrics
        if isinstance(m, STTMetrics):
            turn_metrics["stt_ms"] = m.duration * 1000
            logger.info(
                "call=%s | METRICS STT | duration=%.0fms | audio=%.1fs",
                call_id, m.duration * 1000, m.audio_duration,
            )
            _agent._emit_event(
                "latency.stage",
                call_id=call_id,
                stage="stt",
                duration_ms=round(m.duration * 1000, 3),
                audio_duration_s=round(m.audio_duration, 3),
            )
        elif isinstance(m, LLMMetrics):
            turn_metrics["llm_ttft_ms"] = m.ttft * 1000
            turn_metrics["llm_total_ms"] = m.duration * 1000
            logger.info(
                "call=%s | METRICS LLM | ttft=%.0fms | total=%.0fms | prompt=%d | completion=%d | tok/s=%.0f",
                call_id, m.ttft * 1000, m.duration * 1000,
                m.prompt_tokens, m.completion_tokens, m.tokens_per_second,
            )
            _agent._emit_event(
                "latency.stage",
                call_id=call_id,
                stage="llm",
                ttft_ms=round(m.ttft * 1000, 3),
                total_ms=round(m.duration * 1000, 3),
                prompt_tokens=m.prompt_tokens,
                completion_tokens=m.completion_tokens,
            )
        elif isinstance(m, TTSMetrics):
            ttfb_ms = m.ttfb * 1000
            turn_metrics["tts_ttfb_ms"] = ttfb_ms
            logger.info(
                "call=%s | METRICS TTS | ttfb=%.0fms | total=%.0fms | audio=%.1fs | chars=%d",
                call_id, ttfb_ms, m.duration * 1000,
                m.audio_duration, m.characters_count,
            )
            _agent._emit_event(
                "latency.stage",
                call_id=call_id,
                stage="tts",
                ttfb_ms=round(ttfb_ms, 3),
                total_ms=round(m.duration * 1000, 3),
                audio_duration_s=round(m.audio_duration, 3),
                chars=m.characters_count,
            )
            has_user_turn = bool(turn_metrics.get("user_turn_active"))
            has_turn_pipeline = "eou_ms" in turn_metrics or "llm_ttft_ms" in turn_metrics
            if has_user_turn and has_turn_pipeline:
                eou_ms = turn_metrics.get("eou_ms", 0.0)
                stt_ms = turn_metrics.get("stt_ms", 0.0)
                llm_ttft_ms = turn_metrics.get("llm_ttft_ms", 0.0)
                e2e_ms = eou_ms + stt_ms + llm_ttft_ms + ttfb_ms
                logger.info(
                    "call=%s | METRICS E2E | user_to_first_audio_ms=%.0f | "
                    "eou=%.0fms | stt=%.0fms | llm_ttft=%.0fms | tts_ttfb=%.0fms",
                    call_id, e2e_ms, eou_ms, stt_ms, llm_ttft_ms, ttfb_ms,
                )
                e2e_breached = e2e_ms > TARGET_E2E_FIRST_AUDIO_MS
                if e2e_breached:
                    logger.warning(
                        "call=%s | METRICS E2E SLO BREACH | user_to_first_audio_ms=%.0f | target=%.0f",
                        call_id,
                        e2e_ms,
                        TARGET_E2E_FIRST_AUDIO_MS,
                    )
                _agent._emit_event(
                    "latency.e2e",
                    call_id=call_id,
                    flow=(userdata.active_flow or "").lower(),
                    user_to_first_audio_ms=round(e2e_ms, 3),
                    target_ms=round(TARGET_E2E_FIRST_AUDIO_MS, 3),
                    breach=e2e_breached,
                    eou_ms=round(eou_ms, 3),
                    stt_ms=round(stt_ms, 3),
                    llm_ttft_ms=round(llm_ttft_ms, 3),
                    tts_ttfb_ms=round(ttfb_ms, 3),
                )
                # Reset per-turn metrics so the next user utterance starts clean.
                turn_metrics.clear()
            else:
                logger.debug("call=%s | METRICS E2E skipped | reason=not_user_turn_audio", call_id)
        elif isinstance(m, EOUMetrics):
            turn_metrics["eou_ms"] = m.end_of_utterance_delay * 1000
            logger.info(
                "call=%s | METRICS EOU | eou_delay=%.0fms | transcription=%.0fms",
                call_id, m.end_of_utterance_delay * 1000,
                m.transcription_delay * 1000,
            )
            _agent._emit_event(
                "latency.stage",
                call_id=call_id,
                stage="eou",
                eou_delay_ms=round(m.end_of_utterance_delay * 1000, 3),
                transcription_delay_ms=round(m.transcription_delay * 1000, 3),
            )

    t_start = time.monotonic()
    close_event = asyncio.Event()
    close_state = {"closed": False}
    close_reason = "normal_close"
    last_user_activity_at = t_start
    agent_state = "initializing"
    inactivity_prompt_count = 0
    last_reprompt_at = 0.0
    watchdog_task: asyncio.Task | None = None

    @session.on("close")
    def _on_close(event):
        nonlocal close_reason
        close_reason = f"session_{event.reason}"
        close_event.set()

    @session.on("agent_state_changed")
    def _on_agent_state(event):
        nonlocal agent_state, last_user_activity_at, inactivity_prompt_count, last_reprompt_at
        agent_state = event.new_state
        if event.old_state == "speaking" and event.new_state == "listening":
            last_user_activity_at = time.monotonic()
            inactivity_prompt_count = 0
            last_reprompt_at = 0.0
        logger.info(
            "call=%s | agent_state=%s->%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal last_user_activity_at
        if event.new_state == "speaking":
            last_user_activity_at = time.monotonic()
            userdata.last_user_speech_start_at = last_user_activity_at
        elif event.new_state == "listening":
            now = time.monotonic()
            userdata.last_user_speech_end_at = now
            speech_started_at = getattr(userdata, "last_user_speech_start_at", 0.0) or 0.0
            if (
                event.old_state == "speaking"
                and agent_state == "listening"
                and speech_started_at
                and now - speech_started_at >= 0.25
            ):
                main_agent._say_instant_ack(userdata.turn_count + 1)
        logger.info(
            "call=%s | user_state=%s->%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_input_transcribed")
    def _on_transcribed(event):
        nonlocal last_user_activity_at, inactivity_prompt_count, last_reprompt_at
        transcript = (event.transcript or "").strip()
        if transcript:
            last_user_activity_at = time.monotonic()
            inactivity_prompt_count = 0
            last_reprompt_at = 0.0
            if event.is_final:
                userdata.last_user_final_at = time.monotonic()
                turn_metrics.clear()
                turn_metrics["user_turn_active"] = 1.0
                userdata.last_user_message = transcript[:280]
                _agent._emit_qa_transcript(
                    call_id=call_id,
                    flow=(userdata.active_flow or "").lower(),
                    role="user",
                    text=transcript,
                    is_final=True,
                )
        logger.info(
            "call=%s | transcript final=%s | text=%s",
            call_id, event.is_final, transcript,
        )

    @session.on("agent_false_interruption")
    def _on_false_interruption(event):
        logger.info(
            "call=%s | false_interruption | resumed=%s",
            call_id, event.resumed,
        )

    @session.on("function_tools_executed")
    def _on_tools(event):
        tool_names = [getattr(call, "name", "") for call in event.function_calls]
        logger.info("call=%s | tools=%s", call_id, tool_names)
        metrics = _get_call_metrics(call_id)
        outputs = list(getattr(event, "function_call_outputs", []) or [])
        for idx, name in enumerate(tool_names):
            output = outputs[idx] if idx < len(outputs) else None
            output_text = str(getattr(output, "output", "") or "")
            is_error = bool(getattr(output, "is_error", False))
            success = (not is_error) and not _tool_output_indicates_failure(output_text)
            _agent._emit_event(
                "tool.called",
                call_id=call_id,
                tool=name,
                success=success,
            )
            if name:
                metrics.record_tool_call(name, success=success)

    @session.on("conversation_item_added")
    def _on_conversation_item(event):
        item = getattr(event, "item", None)
        role = str(getattr(item, "role", "") or "").lower()
        if role not in {"assistant", "agent"}:
            return
        text = RestaurantAgent._item_text(item)
        if not text:
            return
        userdata.last_agent_message = text[:280]
        _agent._emit_qa_transcript(
            call_id=call_id,
            flow=(userdata.active_flow or "").lower(),
            role="assistant",
            text=text,
            is_final=True,
        )
        main_agent.schedule_action_review(
            user_text=userdata.last_user_message,
            agent_reply=text,
            source="conversation_item",
        )
        asked_categories = RestaurantAgent.detect_slot_question_categories(text)
        if not asked_categories:
            return
        repeated_categories = RestaurantAgent.record_asked_slot_questions(userdata, text)
        _agent._emit_event(
            "slot.question_asked",
            call_id=call_id,
            flow=(userdata.active_flow or "").lower(),
            categories=sorted(asked_categories),
            repeated=bool(repeated_categories),
        )
        if repeated_categories:
            captured_repeated_categories = [
                category for category in repeated_categories
                if RestaurantAgent._slot_is_captured(category, userdata)
            ]
            if captured_repeated_categories:
                userdata.pending_corrective_response = (
                    RestaurantAgent.corrective_response_for_repeated_questions(
                        userdata,
                        captured_repeated_categories,
                    )
                )
                userdata.pending_corrective_turn = userdata.turn_count
                userdata.pending_corrective_source = "slot_repetition"
            metrics = _get_call_metrics(call_id)
            for category in repeated_categories:
                metrics.record_repetition(category)
            logger.warning(
                "call=%s | repeated slot question detected | categories=%s",
                call_id,
                repeated_categories,
            )
            _agent._emit_event(
                "slot.repeated_question_detected",
                call_id=call_id,
                flow=(userdata.active_flow or "").lower(),
                categories=repeated_categories,
            )

    @session.on("error")
    def _on_error(event):
        from backend.client import exc_log_fields as _exc_log_fields
        logger.error("call=%s | session error | %s", call_id, _exc_log_fields(event.error))

    async def _watch_inactivity() -> None:
        nonlocal inactivity_prompt_count, last_reprompt_at, close_reason
        while not close_event.is_set():
            await asyncio.sleep(1.0)
            if close_event.is_set():
                return
            if agent_state in {"speaking", "thinking", "initializing"}:
                continue
            if userdata.session_transitional_state:
                continue
            if (
                userdata.order_submit_in_flight
                or userdata.reservation_submit_in_flight
                or userdata.complaint_submit_in_flight
            ):
                continue

            idle_for = time.monotonic() - last_user_activity_at

            # If the call has already produced its outcome (order/reservation/
            # complaint confirmed), there is nothing more to do — close
            # gracefully with a goodbye instead of asking "are you still
            # there?" which sounds rude after the customer just said thanks.
            call_completed = (
                userdata.order_confirmed
                or userdata.reservation_confirmed
                or userdata.complaint_logged
            )
            if call_completed and idle_for >= _agent.NO_SPEECH_PROMPT_SECONDS:
                close_reason = "completed_idle"
                userdata.session_transitional_state = True
                logger.info(
                    "call=%s | completed-idle close | idle_for=%.1fs",
                    call_id, idle_for,
                )
                _agent._emit_event(
                    "call.inactivity",
                    call_id=call_id,
                    action="completed_close",
                    idle_for_s=round(idle_for, 3),
                )
                await _agent._safe_close_session_once(
                    session,
                    close_state,
                    farewell="نورتنا يا فندم، يا هلا تاني.",
                )
                return

            if (
                inactivity_prompt_count < _agent.NO_SPEECH_REPROMPT_LIMIT
                and idle_for >= _agent.NO_SPEECH_PROMPT_SECONDS
                and (not last_reprompt_at or (time.monotonic() - last_reprompt_at) >= _agent.NO_SPEECH_REPROMPT_GAP_SECONDS)
            ):
                inactivity_prompt_count += 1
                last_reprompt_at = time.monotonic()
                logger.warning(
                    "call=%s | inactivity reprompt | idle_for=%.1fs | count=%d",
                    call_id, idle_for, inactivity_prompt_count,
                )
                # Use the LLM-tracked intent from set_intent rather than the
                # agent class name (which is always "restaurantagent" in the
                # single-agent design). Lets _inactivity_reprompt produce
                # flow-specific nudges like "تحب تطلب إيه؟" when relevant.
                flow_name = (userdata.active_flow or "").strip().lower()
                _agent._emit_event(
                    "call.inactivity",
                    call_id=call_id,
                    flow=flow_name,
                    action="reprompt",
                    idle_for_s=round(idle_for, 3),
                    count=inactivity_prompt_count,
                )
                reprompt_text = _agent._inactivity_reprompt(
                    userdata, flow_name, prompt_count=inactivity_prompt_count,
                )
                with contextlib.suppress(Exception):
                    userdata.last_agent_message = reprompt_text
                    from utils.voice import say_safe as _say_safe
                    await _say_safe(
                        session,
                        reprompt_text,
                        allow_interruptions=True,
                        add_to_chat_ctx=False,
                    )
                continue

            if idle_for >= _agent.NO_SPEECH_CLOSE_SECONDS:
                close_reason = "inactivity_timeout"
                userdata.session_transitional_state = True
                logger.warning(
                    "call=%s | inactivity close | idle_for=%.1fs",
                    call_id, idle_for,
                )
                # Use the LLM-tracked intent from set_intent rather than the
                # agent class name (which is always "restaurantagent" in the
                # single-agent design). Lets _inactivity_reprompt produce
                # flow-specific nudges like "تحب تطلب إيه؟" when relevant.
                flow_name = (userdata.active_flow or "").strip().lower()
                _agent._emit_event(
                    "call.inactivity",
                    call_id=call_id,
                    flow=flow_name,
                    action="timeout",
                    idle_for_s=round(idle_for, 3),
                )
                await _agent._safe_close_session_once(
                    session,
                    close_state,
                    farewell="هقفل المكالمة دلوقتي، كلمنا تاني في أي وقت يا فندم.",
                )
                return

    try:
        # Block briefly for the TTS warmup so the very first audio frame
        # (the opening line in on_enter) doesn't pay the cold-handshake
        # cost. Capped tight: if warmup hasn't finished within the budget,
        # proceed anyway — the cold path is slow but functional. Skipped
        # in realtime mode (unified model self-warms on connect).
        if tts_warmup_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(tts_warmup_task),
                    timeout=_agent._get_env_float("TTS_WARMUP_AWAIT_SECONDS", 0.6, min_value=0.0),
                )
            except asyncio.TimeoutError:
                logger.info("call=%s | tts warmup not ready in budget; proceeding cold", call_id)
            except Exception as exc:
                logger.warning("call=%s | tts warmup await error | %s", call_id, exc)
        await session.start(
            agent=main_agent,
            room=ctx.room,
            room_input_options=_build_room_input_options(realtime_mode=realtime_mode),
        )
        watchdog_task = asyncio.create_task(_watch_inactivity(), name=f"inactivity_watchdog_{call_id}")
        await asyncio.wait_for(close_event.wait(), timeout=MAX_CALL_DURATION)
    except asyncio.TimeoutError:
        close_reason = "call_timeout"
        userdata.session_transitional_state = True
        logger.warning("call=%s | timeout after %ds — ending session", call_id, MAX_CALL_DURATION)
        await _agent._safe_close_session_once(
            session,
            close_state,
            farewell="معلش يا فندم، وقت المكالمة خلص. كلمنا تاني في أي وقت.",
        )
    except Exception as exc:
        close_reason = "session_error"
        logger.exception("call=%s | error: %s", call_id, exc)
        userdata.session_transitional_state = True
        await _agent._safe_aclose_session_once(session, close_state)
        raise
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task
        for task in (llm_warmup_task, backend_warmup_task, tts_warmup_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for task in list(userdata.background_tasks):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        userdata.background_tasks.clear()
        await _agent._safe_aclose_session_once(session, close_state)
        await _agent._release_session_slot(call_id)
        _agent._cleanup_turn_count(call_id)
        duration = int(time.monotonic() - t_start)
        call_ended_at_utc = datetime.now(timezone.utc)
        try:
            call_log_result = await _agent.submit_call_log(
                userdata,
                close_reason=close_reason,
                duration_seconds=duration,
                started_at_iso=call_started_at_utc.isoformat(),
                ended_at_iso=call_ended_at_utc.isoformat(),
            )
            if call_log_result is not None and call_log_result.get("queued"):
                logger.warning("call=%s | call log deferred to queue", call_id)
        except Exception:
            logger.exception("call=%s | call log submit failed", call_id)
        # If the agent invoked end_call(reason), surface it as the close_reason
        # so call.end telemetry distinguishes graceful agent hangups from
        # participant disconnects, errors, and timeouts.
        if userdata.end_call_reason:
            close_reason = f"end_call:{userdata.end_call_reason}"
        _agent._emit_event(
            "call.end",
            call_id=call_id,
            flow=(userdata.active_flow or "").lower(),
            duration_s=duration,
            close_reason=close_reason,
            order_confirmed=userdata.order_confirmed,
            reservation_confirmed=userdata.reservation_confirmed,
            complaint_logged=userdata.complaint_logged,
            config_source=userdata.restaurant.config_source,
        )
        if userdata.order_confirmed:
            _outcome_reason = "order_submitted"
        elif userdata.reservation_confirmed:
            _outcome_reason = "reservation_submitted"
        elif userdata.complaint_logged:
            _outcome_reason = "complaint_submitted"
        else:
            _outcome_reason = close_reason or "unknown"
        _metrics = _get_call_metrics(call_id)
        _metrics.final_intent = (userdata.active_flow or "").lower()
        _finalize_call_metrics(call_id, reason=_outcome_reason)
        logger.info(
            "call=%s | ended | duration=%ds | close_reason=%s | order=%s | reservation=%s | complaint=%s | config_source=%s",
            call_id, duration, close_reason, userdata.order_confirmed, userdata.reservation_confirmed,
            userdata.complaint_logged, userdata.restaurant.config_source,
        )

if __name__ == "__main__":
    _start_parent_health_server()
    cli.run_app(server)
