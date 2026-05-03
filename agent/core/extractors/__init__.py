"""Production-grade deterministic extractors for the dialogue engine.

Each extractor takes raw user text plus relevant context (menu, restaurant
config, current state) and returns a structured result with a confidence
score. The dialogue engine decides what to do based on the score:

- HIGH    → capture deterministically.
- MEDIUM  → ask a targeted clarification question.
- LOW     → fall back to LLM or reprompt.

Phase 2 introduces ``order_extractor``. Phase 3 will add intent, contact,
address, reservation and complaint extractors.
"""

from __future__ import annotations
