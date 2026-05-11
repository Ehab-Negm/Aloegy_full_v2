"""Heuristic transcript review for market QA batches.

This is not a replacement for human listening. It catches obvious issues in
opt-in `qa.transcript` telemetry: repeated assistant messages, long responses,
and assistant turns that ask several questions at once.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from nlp.arabic import normalize_ar
from qa_telemetry_gate import load_telemetry_events


@dataclass
class TranscriptReviewResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    transcript_events: int = 0
    assistant_turns: int = 0
    user_turns: int = 0
    repeated_assistant_messages: int = 0
    multi_question_assistant_turns: int = 0
    long_assistant_turns: int = 0
    redacted_phone_turns: int = 0


def _question_count(text: str) -> int:
    return text.count("?") + text.count("\u061f")


def _word_count(text: str) -> int:
    return len([part for part in re.split(r"\s+", text.strip()) if part])


def evaluate_transcript_events(
    events: Iterable[dict[str, Any]],
    *,
    max_assistant_words: int = 35,
    max_questions_per_turn: int = 1,
) -> TranscriptReviewResult:
    transcripts = [
        event for event in events
        if event.get("event") == "qa.transcript"
    ]
    assistant_events = [
        event for event in transcripts
        if str(event.get("role") or "").lower() == "assistant"
    ]
    user_events = [
        event for event in transcripts
        if str(event.get("role") or "").lower() == "user"
    ]

    repeated_assistant = 0
    multi_question = 0
    long_assistant = 0
    seen_by_call: dict[str, set[str]] = {}
    for event in assistant_events:
        call_id = str(event.get("call_id") or "")
        text = str(event.get("text") or "")
        normalized = normalize_ar(text)
        if normalized:
            seen = seen_by_call.setdefault(call_id, set())
            if normalized in seen:
                repeated_assistant += 1
            seen.add(normalized)
        if _question_count(text) > max_questions_per_turn:
            multi_question += 1
        if _word_count(text) > max_assistant_words:
            long_assistant += 1

    redacted_phone_turns = sum(
        1 for event in transcripts
        if "[redacted_phone" in str(event.get("text") or "")
    )

    reasons: list[str] = []
    if not transcripts:
        reasons.append("no qa.transcript events found; enable QA_TRANSCRIPT_EVENTS_ENABLED for transcript review")
    if repeated_assistant:
        reasons.append(f"repeated assistant messages found: {repeated_assistant}")
    if multi_question:
        reasons.append(f"assistant turns with too many questions: {multi_question}")
    if long_assistant:
        reasons.append(f"assistant turns over {max_assistant_words} words: {long_assistant}")

    return TranscriptReviewResult(
        passed=not reasons,
        reasons=reasons,
        transcript_events=len(transcripts),
        assistant_turns=len(assistant_events),
        user_turns=len(user_events),
        repeated_assistant_messages=repeated_assistant,
        multi_question_assistant_turns=multi_question,
        long_assistant_turns=long_assistant,
        redacted_phone_turns=redacted_phone_turns,
    )


def _render_result(result: TranscriptReviewResult) -> dict[str, Any]:
    return {
        "passed": result.passed,
        "reasons": result.reasons,
        "transcript_events": result.transcript_events,
        "assistant_turns": result.assistant_turns,
        "user_turns": result.user_turns,
        "repeated_assistant_messages": result.repeated_assistant_messages,
        "multi_question_assistant_turns": result.multi_question_assistant_turns,
        "long_assistant_turns": result.long_assistant_turns,
        "redacted_phone_turns": result.redacted_phone_turns,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review QA transcript telemetry.")
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--max-assistant-words", type=int, default=35)
    parser.add_argument("--max-questions-per-turn", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.telemetry.exists():
        result = TranscriptReviewResult(
            passed=False,
            reasons=[f"telemetry file not found: {args.telemetry}"],
        )
    else:
        result = evaluate_transcript_events(
            load_telemetry_events(args.telemetry),
            max_assistant_words=args.max_assistant_words,
            max_questions_per_turn=args.max_questions_per_turn,
        )
    print(json.dumps(_render_result(result), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
