from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DialogueActionType = Literal[
    "say",
    "ask_slot",
    "confirm",
    "capture_order",
    "capture_name",
    "capture_phone",
    "capture_address",
    "submit",
    "handoff",
    "no_action",
]

QuestionCategory = Literal[
    "order",
    "name",
    "phone",
    "address",
    "reservation_time",
    "guests",
    "branch",
    "complaint",
    "complaint_type",
    "confirmation",
    "post_completion",
    "unknown",
]


@dataclass(frozen=True)
class DialogueAction:
    type: DialogueActionType
    message: str = ""
    question_category: QuestionCategory | None = None
    slot: str | None = None
    critical: bool = False
    uses_llm: bool = False


NO_ACTION = DialogueAction(type="no_action")
