"""Monkey-patch livekit-plugins-google so tool results actually reach
``gemini-3.1-flash-live-preview``.

The vendored plugin (``livekit-plugins-google==1.5.2``) bails out of
``RealtimeSession.update_chat_ctx`` for the 3.1 Live model with::

    if self._opts.model == "gemini-3.1-flash-live-preview":
        logger.warning("update_chat_ctx is not compatible ... and will be ignored.")
        self._chat_ctx = chat_ctx.copy(...)
        return  # ← bails BEFORE sending tool results to the model

The framework delivers function-tool responses by appending them to the
chat context and calling ``update_chat_ctx``. Because the plugin returns
early, the synthesized ``LiveClientToolResponse`` is **never** dispatched
to the model — the agent gets stuck in ``thinking`` after every tool call
and the customer hears silence.

This patch reinstates only the tool-result delivery for that branch (it
still skips the persona/history rewrite, which 3.1 Live genuinely doesn't
accept). Persona, instructions, and full chat history continue to be
ignored as the plugin intends; only the function-response path is
restored.

Apply the patch once at process start by importing this module before any
``RealtimeSession`` is created — ``agent.py`` does that next to its other
plugin setup.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("restaurant.agent")

_PATCHED_FLAG = "_aloegy_3_1_tool_response_patch_applied"


def apply() -> bool:
    """Install the patch. Idempotent — safe to call multiple times.

    Returns ``True`` when the patch was applied this call, ``False`` when it
    was already in place or the plugin internals don't match the expected
    layout (e.g. on a future upgrade that fixes this server-side).
    """
    try:
        from livekit.plugins.google.realtime import realtime_api as _rt
    except Exception as exc:  # pragma: no cover — import-time issue
        logger.debug("realtime_patch: could not import realtime_api | %s", exc)
        return False

    target_cls = getattr(_rt, "RealtimeSession", None)
    if target_cls is None:
        logger.debug("realtime_patch: RealtimeSession class not found")
        return False

    if getattr(target_cls, _PATCHED_FLAG, False):
        return False

    try:
        from livekit.agents import llm as _lkllm
        from livekit.plugins.google.utils import get_tool_results_for_realtime
    except Exception as exc:  # pragma: no cover
        logger.debug("realtime_patch: helper imports failed | %s", exc)
        return False

    original_update_chat_ctx = target_cls.update_chat_ctx

    async def patched_update_chat_ctx(
        self: Any, chat_ctx: _lkllm.ChatContext  # type: ignore[name-defined]
    ) -> None:
        # Only intercept the 3.1 Live early-return branch. Everything else
        # delegates to the upstream implementation untouched.
        if getattr(self, "_opts", None) is None or self._opts.model != "gemini-3.1-flash-live-preview":
            return await original_update_chat_ctx(self, chat_ctx)

        # Reproduce the diff computation the upstream method does for other
        # models so we know which items are *new* in this update — function
        # responses live among them.
        try:
            diff_ops = _lkllm.utils.compute_chat_ctx_diff(self._chat_ctx, chat_ctx)
        except Exception as exc:  # pragma: no cover
            logger.warning("realtime_patch: chat_ctx diff failed | %s", exc)
            self._chat_ctx = chat_ctx.copy(
                exclude_handoff=True,
                exclude_instructions=True,
                exclude_empty_message=True,
                exclude_config_update=True,
            )
            return

        append_ctx = _lkllm.ChatContext.empty()
        for _, item_id in diff_ops.to_create:
            item = chat_ctx.get_by_id(item_id)
            if item is not None:
                append_ctx.items.append(item)

        if append_ctx.items:
            try:
                tool_results = get_tool_results_for_realtime(
                    append_ctx,
                    vertexai=getattr(self._opts, "vertexai", False),
                    tool_response_scheduling=getattr(
                        self._opts, "tool_response_scheduling", None
                    ),
                )
            except TypeError:
                # Older signatures might not accept tool_response_scheduling.
                tool_results = get_tool_results_for_realtime(
                    append_ctx, vertexai=getattr(self._opts, "vertexai", False)
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("realtime_patch: get_tool_results failed | %s", exc)
                tool_results = None

            if tool_results is not None:
                try:
                    self._send_client_event(tool_results)
                    logger.debug(
                        "realtime_patch: dispatched tool results to 3.1 Live | "
                        "items=%d",
                        len(append_ctx.items),
                    )
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "realtime_patch: failed to send tool results | %s", exc
                    )

        # Track the new context locally — same as the original code path —
        # so subsequent diffs are computed against the right baseline.
        self._chat_ctx = chat_ctx.copy(
            exclude_handoff=True,
            exclude_instructions=True,
            exclude_empty_message=True,
            exclude_config_update=True,
        )

    target_cls.update_chat_ctx = patched_update_chat_ctx
    setattr(target_cls, _PATCHED_FLAG, True)
    logger.info(
        "realtime_patch: installed tool-response delivery shim for "
        "gemini-3.1-flash-live-preview"
    )
    return True
