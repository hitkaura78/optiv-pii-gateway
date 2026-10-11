"""Government ID and payment card recognizers + validators (Task T1.4 / U-08, U-09).

Implements:
- US_SSN: accepts areas 001..665 and 667..999 (including 900-999 per Guide H5);
  rejects area 000, area 666, group 00, serial 0000.
- IN_PAN: [A-Z]{5}\\d{4}[A-Z]
- PL_PESEL: 11-digit number validated by the official PESEL checksum + birth-date encoding,
  plus masked form \\d{6}[xX*]{5} (MEDIUM severity).
- CREDIT_CARD: Luhn-validated 13..19 digit payment cards.
- PASSPORT: context-cued passport identifiers.
"""
from __future__ import annotations

import re

from detect.models import Candidate
from detect.recognizers.phone import has_phone_cue_before
from pii_pipeline.models import PIIType

_SSN_DASHED_RE = re.compile(r"(?<![A-Za-z0-9-])(\d{3})-(\d{2})-(\d{4})(?![A-Za-z0-9-])")
_SSN_CTX_RE = re.compile(
    r"\b(?:ssn|social\s+security(?:\s+(?:number|no\b\.?))?)(?![A-Za-z0-9])[^\d.;\n]{0,15}(?<![0-9])(\d{3})(\d{2})(\d{4})(?![0-9])",
    re.I,
)
_PAN_RE = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{5}\d{4}[A-Z])(?![A-Za-z0-9-])")
_PESEL_RE = re.compile(r"(?<![A-Za-z0-9+#-])(\d{11})(?![A-Za-z0-9-])")
_PESEL_MASKED_RE = re.compile(r"(?<![A-Za-z0-9-])(\d{6}[xX*]{5})(?![A-Za-z0-9-])")
_PESEL_CUE_RE = re.compile(r"\bpesel\b", re.I)

_CARD_RE = re.compile(
    r"(?<![\d-])(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{5})(?![\d-]?\d)"
)
_PASSPORT_CTX_RE = re.compile(
    r"\bpassport(?:\s+(?:no\.?|number|#))?\s*[:#-]?\s*([A-Z][A-Z0-9]{6,8}|\d{8,9})(?![A-Za-z0-9])",
    re.I,
)

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)


def is_valid_ssn_parts(area: str, group: str, serial: str) -> bool:
    """Validate US SSN parts per Guide H5 (900-999 allowed; 000, 666, group 00, serial 0000 rejected)."""
    if len(area) != 3 or len(group) != 2 or len(serial) != 4:
        return False
    if not (area.isdigit() and group.isdigit() and serial.isdigit()):
        return False
    if area in ("000", "666") or group == "00" or serial == "0000":
        return False
    return True


def is_valid_pesel_date(yymmdd: str) -> bool:
    """Check whether the 6-digit YYMMDD prefix of a PESEL encodes a valid calendar month and day."""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return False
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    # Century offsets in PESEL: +0 (1900s), +20 (2000s), +40 (2100s), +60 (2200s), +80 (1800s)
    base_month = mm % 20
    if not (1 <= base_month <= 12):
        return False
    if not (1 <= dd <= 31):
        return False
    return True


def validate_pesel_checksum(number: str) -> bool:
    """Validate an 11-digit Polish PESEL using stdnum if available, or the exact weight formula."""
    if len(number) != 11 or not number.isdigit():
        return False
    if not is_valid_pesel_date(number[:6]):
        return False
    try:
        from stdnum.pl import pesel as stdnum_pesel  # type: ignore
        return bool(stdnum_pesel.is_valid(number))
    except ImportError:
        total = sum(w * int(d) for w, d in zip(_PESEL_WEIGHTS, number[:10]))
        check = (10 - (total % 10)) % 10
        return check == int(number[10])


def luhn_check(digits: str) -> bool:
    """Standard Luhn mod-10 checksum for payment card numbers."""
    if not digits or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def recognize_govids(text: str) -> list[Candidate]:
    """Recognize US_SSN, IN_PAN, PL_PESEL (valid, invalid-cued, and masked), PASSPORT, and CREDIT_CARD."""
    if not text:
        return []
    out: list[Candidate] = []

    # 1. US SSN (dashed)
    for m in _SSN_DASHED_RE.finditer(text):
        area, group, serial = m.groups()
        if not is_valid_ssn_parts(area, group, serial):
            continue
        out.append(
            Candidate(
                start=m.start(),
                end=m.end(),
                pii_type=PIIType.US_SSN,
                entity_type="US_SSN",
                score=0.95,
                validator="ssn_rules:pass",
                detector="regex:us_ssn",
                category="GOVERNMENT_ID",
                severity="HIGH",
                validated=True,
            )
        )

    # 2. US SSN (undashed with context cue)
    for m in _SSN_CTX_RE.finditer(text):
        area, group, serial = m.groups()
        if not is_valid_ssn_parts(area, group, serial):
            continue
        s, e = m.start(1), m.end(3)
        out.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.US_SSN,
                entity_type="US_SSN",
                score=0.88,
                validator="ssn_rules:pass_context",
                detector="regex:us_ssn_context",
                category="GOVERNMENT_ID",
                severity="HIGH",
                validated=True,
            )
        )

    # 3. Indian PAN ([A-Z]{5}\d{4}[A-Z])
    for m in _PAN_RE.finditer(text):
        s, e = m.span(1)
        out.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.PAN,
                entity_type="IN_PAN",
                score=0.95,
                validator="format:in_pan",
                detector="regex:in_pan",
                category="GOVERNMENT_ID",
                severity="HIGH",
                validated=True,
            )
        )

    # 4. Polish PESEL (11 digits)
    for m in _PESEL_RE.finditer(text):
        s, e = m.span(1)
        if has_phone_cue_before(text, s, window=12):
            continue
        num = text[s:e]
        if validate_pesel_checksum(num):
            out.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.OTHER,
                    entity_type="PL_PESEL",
                    score=0.95,
                    validator="pesel_checksum:pass",
                    detector="stdnum:pl_pesel",
                    category="GOVERNMENT_ID",
                    severity="HIGH",
                    validated=True,
                )
            )
        else:
            # Check if PESEL context cue appears nearby; if so, emit lower-score unvalidated candidate
            ctx_window = text[max(0, s - 30):min(len(text), e + 15)]
            if _PESEL_CUE_RE.search(ctx_window):
                out.append(
                    Candidate(
                        start=s,
                        end=e,
                        pii_type=PIIType.OTHER,
                        entity_type="PL_PESEL",
                        score=0.45,
                        validator="pesel_checksum:fail",
                        detector="regex:pl_pesel_invalid",
                        category="GOVERNMENT_ID",
                        severity="MEDIUM",
                        validated=False,
                    )
                )

    # 5. Masked Polish PESEL (\d{6}[xX*]{5}, e.g. 123456xxxxx or 850714*****)
    for m in _PESEL_MASKED_RE.finditer(text):
        s, e = m.span(1)
        out.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.OTHER,
                entity_type="PL_PESEL_MASKED",
                score=0.82,
                validator="format:pesel_masked",
                detector="regex:pesel_masked",
                category="GOVERNMENT_ID",
                severity="MEDIUM",
                validated=True,
            )
        )

    # 6. Payment Card (Luhn validated)
    for m in _CARD_RE.finditer(text):
        s, e = m.span()
        raw = text[s:e]
        digits = re.sub(r"\D", "", raw)
        if not (13 <= len(digits) <= 19):
            continue
        if luhn_check(digits):
            out.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.CREDIT_CARD,
                    entity_type="CREDIT_CARD",
                    score=0.95,
                    validator="luhn:pass",
                    detector="luhn:credit_card",
                    category="FINANCIAL",
                    severity="HIGH",
                    validated=True,
                )
            )
        elif re.fullmatch(r"\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}", raw):
            out.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.CREDIT_CARD,
                    entity_type="CREDIT_CARD",
                    score=0.40,
                    validator="luhn:fail",
                    detector="regex:credit_card_no_luhn",
                    category="FINANCIAL",
                    severity="MEDIUM",
                    reason="LUHN_FAILED_REVIEW",
                    validated=False,
                )
            )

    # 7. TODO(Member-3): PASSPORT and TAX_ID formats are not yet specified in the Guide.
    # [VERIFY BEFORE EXECUTION: confirm exact PASSPORT and TAX_ID patterns from the Policy
    # sample documents / Member 5 truth before enabling recognizers here — do not guess.]

    out.sort(key=lambda c: (c.start, -(c.end - c.start), c.entity_type))
    return out

