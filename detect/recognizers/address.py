"""Home / street address recognizer (Task T2.1 / U-12).

Matches physical street addresses (number + street name + suffix + optional unit/city/state/postal).
Corporate HQ addresses matched here are subsequently suppressed by the allow-list layer
in detect/resolver.py with reason code ALLOW_HQ_ADDRESS.
"""
from __future__ import annotations

import re

from detect.models import Candidate
from pii_pipeline.models import PIIType

_STREET_SUFFIX = (
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|"
    r"Drive|Dr\.?|Court|Ct\.?|Way|Terrace|Ter\.?|Place|Pl\.?|Circle|Cir\.?|Parkway|Pkwy\.?)"
)

_ADDRESS_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"(\d{{1,5}}\s+[A-Z][A-Za-z0-9.\s]{{1,35}}?\s+{_STREET_SUFFIX}"
    rf"(?:,\s*(?:Suite|Ste\.?|Apt\.?|Unit|#)\s*[A-Za-z0-9-]+)?"
    rf"(?:,\s*[A-Z][A-Za-z.\s]{{1,25}}(?:,\s*[A-Z]{{2}}\s+\d{{5}}(?:-\d{{4}})?|\s+[A-Z]{{1,2}}\d[A-Z\d]?\s*\d[A-Z]{{2}})?)?)"
    rf"(?![A-Za-z0-9])"
)


def recognize_addresses(text: str) -> list[Candidate]:
    """Recognize street/home addresses in `text`."""
    if not text:
        return []
    out: list[Candidate] = []
    for m in _ADDRESS_RE.finditer(text):
        s, e = m.span(1)
        out.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.ADDRESS,
                entity_type="HOME_ADDRESS",
                score=0.85,
                validator="syntax:street_address",
                detector="regex:home_address",
                category="HOME_ADDRESS",
                severity="HIGH",
                validated=True,
            )
        )
    return out

