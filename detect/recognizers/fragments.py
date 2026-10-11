"""Financial and SSN last-four fragment recognizer (Task T2.1 / H8 / U-11).

Detects 4-digit fragments (CARD_LAST4, ACCOUNT_LAST4, SSN_LAST4) when triggered by
'ending', 'ending in', 'last 4', 'last four', or an opening parenthesis '(' within
`last4_context_window_chars` (default 30) characters of 'card', 'account'/'acct',
or 'ssn'/'social security'.
"""
from __future__ import annotations

import re
from typing import Any

from detect.config import load_config
from detect.models import Candidate
from pii_pipeline.models import PIIType

_DOMAIN_CUE_RE = re.compile(
    r"\b(?P<kind>card|credit\s+card|debit\s+card|account|acct|ssn|social\s+security)\b",
    re.I,
)

# Trigger followed by 4 digits: e.g. "ending 2218", "ending in 2218", "ending #2218",
# "last 4: 2218", "(2218)", "(x2218)", "(**2218)", "(****2218)"
_TRIGGER_FOUR_RE = re.compile(
    r"(?:(?:\bending(?:\s+in)?|\blast\s+(?:4|four))\s*[:#-]?\s*(?:[xX*]{1,8}[ -]?)?|\(\s*(?:[xX*]{1,8}[ -]?)?)"
    r"(?P<digits>\d{4})(?!\d)",
    re.I,
)


def _kind_to_entity_type(kind_str: str) -> str:
    k = kind_str.lower()
    if "card" in k:
        return "CARD_LAST4"
    if "account" in k or "acct" in k:
        return "ACCOUNT_LAST4"
    return "SSN_LAST4"


def recognize_fragments(text: str, cfg: Any = None) -> list[Candidate]:
    """Recognize context-cued 4-digit financial/SSN fragments."""
    if not text:
        return []
    resolved_cfg = load_config(cfg)
    window = resolved_cfg.detect.last4_context_window_chars

    cues = list(_DOMAIN_CUE_RE.finditer(text))
    if not cues:
        return []

    out: list[Candidate] = []
    for m in _TRIGGER_FOUR_RE.finditer(text):
        trig_start = m.start()
        s, e = m.span("digits")
        chosen_kind: str | None = None
        best_dist: int | None = None
        for c in cues:
            # Pick the closest domain cue preceding the trigger within `window` chars
            if c.end() <= s and 0 <= (trig_start - c.end()) <= window:
                dist = s - c.end()
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    chosen_kind = _kind_to_entity_type(c.group("kind"))
        if chosen_kind is not None:
            pii_type = PIIType.CREDIT_CARD if chosen_kind == "CARD_LAST4" else (
                PIIType.US_SSN if chosen_kind == "SSN_LAST4" else PIIType.OTHER
            )
            out.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=pii_type,
                    entity_type=chosen_kind,
                    score=0.80,
                    validator="context:last4_cue_30",
                    detector=f"context:{chosen_kind.lower()}",
                    category="FINANCIAL_FRAGMENT",
                    severity="MEDIUM",
                    validated=True,
                )
            )
    return out
