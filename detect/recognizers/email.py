"""Email recognizer with OCR-tolerant domain syntax matching (Task T1.1 / U-06).

Matches email syntax rather than a static TLD list so OCR-damaged domains
(e.g. 'examp1e.c0m', 'company.0rg') still match, while rejecting 'a @ b'
and invalid label boundaries.
"""
from __future__ import annotations

import re

from detect.models import Candidate
from pii_pipeline.models import PIIType

# Local part: alphanumeric + ._%+- (no leading/trailing dot, no consecutive dots)
# Domain part: at least two labels separated by '.', each starting/ending with alphanumeric,
# TLD at least 2 alphanumeric chars (digits allowed for OCR damage like 'c0m', '0rg').
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"([A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+)*"
    r"@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)"
    r"(?![A-Za-z0-9_-])"
)

_STD_TLD_RE = re.compile(r"^[A-Za-z]{2,24}$")


def recognize_emails(text: str) -> list[Candidate]:
    """Return deterministic email candidates with offsets into `text`."""
    if not text or "@" not in text:
        return []
    out: list[Candidate] = []
    for m in _EMAIL_RE.finditer(text):
        s, e = m.span(1)
        matched = text[s:e]
        local, _, domain = matched.partition("@")
        if not local or not domain or ".." in matched:
            continue
        labels = domain.split(".")
        tld = labels[-1]
        if len(tld) < 2:
            continue
        # Reject purely numeric domains like @1.2 (not an email domain)
        if all(lbl.isdigit() for lbl in labels):
            continue
        if not any(ch.isalpha() for ch in domain):
            continue
        is_std = bool(_STD_TLD_RE.match(tld)) and all(
            any(c.isalpha() for c in lbl) for lbl in labels
        )
        score = 0.95 if is_std else 0.88
        validator = "syntax:email" if is_std else "syntax:email_ocr_domain"
        out.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.EMAIL,
                entity_type="EMAIL_ADDRESS",
                score=score,
                validator=validator,
                detector="regex:email",
                category="CONTACT",
                severity="MEDIUM",
                validated=True,
            )
        )
    return out

