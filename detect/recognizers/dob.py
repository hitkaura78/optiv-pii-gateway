"""Date of Birth (DOB) context recognizer (Task T2.1 / U-11).

Rule: A date is classified as DATE_OF_BIRTH only if a DOB cue ('DOB', 'D.O.B.',
'date of birth', 'birth date', 'birthdate', 'born on', 'born') appears within
`dob_context_window_chars` (default 25) characters BEFORE the start of the date.
Supported date formats:
- Day Month Year: '14 July 1991', '1 Jul 1985', '14-Jul-1991'
- Month Day, Year: 'July 14, 1991', 'Jul 14 1991'
- ISO: '1991-07-14'
- Slash / Dot / Dash numeric: '07/14/1991', '14/07/1991', '14.07.1991'
"""
from __future__ import annotations

import re
from typing import Any

from detect.config import load_config
from detect.models import Candidate
from pii_pipeline.models import PIIType

_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

DATE_PATTERN = (
    rf"(?:"
    rf"\d{{1,2}}(?:[\s-]+){_MONTHS}(?:[\s,-]+)\d{{4}}"
    rf"|{_MONTHS}\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}"
    rf"|\d{{4}}[-/.](?:0[1-9]|1[0-2])[-/.](?:0[1-9]|[12]\d|3[01])"
    rf"|(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.]\d{{4}}"
    rf"|(?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])[-/.]\d{{4}}"
    rf")"
)

_DATE_RE = re.compile(rf"(?<![A-Za-z0-9])({DATE_PATTERN})(?![A-Za-z0-9])", re.I)
_DOB_CUE_RE = re.compile(
    r"\b(?:d\.?o\.?b\.?|date\s+of\s+birth|birth\s*date|born(?:\s+on)?)\b",
    re.I,
)


def recognize_dob(text: str, cfg: Any = None) -> list[Candidate]:
    """Recognize dates preceded within <= 25 chars by a DOB cue."""
    if not text:
        return []
    resolved_cfg = load_config(cfg)
    window = resolved_cfg.detect.dob_context_window_chars
    out: list[Candidate] = []

    cues = list(_DOB_CUE_RE.finditer(text))
    if not cues:
        return []

    for m in _DATE_RE.finditer(text):
        s, e = m.span(1)
        # Check if any DOB cue appears before `s` such that the distance from cue start
        # (or cue end) to `s` is within `window` characters (`s - cue.start() <= window`
        # or `s - cue.end() <= window`, where cue.end() <= s).
        # Specifically: "within 25 characters BEFORE it" means the gap/prefix between the cue
        # and the date start is <= 25 characters (`0 <= s - cue.end() <= window` and cue precedes date).
        matched_cue = False
        for c in cues:
            if c.end() <= s and (s - c.end()) <= window:
                matched_cue = True
                break
        if matched_cue:
            out.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.DATE_OF_BIRTH,
                    entity_type="DATE_OF_BIRTH",
                    score=0.90,
                    validator="context:dob_cue_25",
                    detector="context:dob",
                    category="DOB",
                    severity="HIGH",
                    validated=True,
                )
            )
    return out

