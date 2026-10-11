"""Phone number recognizer across regions + OCR-lenient fallback (Task T1.3 / U-07).

Supports US, GB, IN, FR, PL, SG, SE, HK formats plus a lenient fallback that
accepts '+' or OCR-damaged '#' prefix with >= 8 digits so test/directory numbers
such as '+1 (555) 0114' and '#1 (555) 0114' are reliably detected.
Deduplicates overlapping spans deterministically.
"""
from __future__ import annotations

import re
from typing import Any

from detect.config import load_config
from detect.models import Candidate
from pii_pipeline.models import PIIType

# Structured international or regional phone patterns:
# 1) '+' or OCR '#' followed by country code and 2..5 groups of digits (>= 8 digits total)
# 2) NANP: optional +1/1, (NXX) or NXX, separator, NXX, separator, XXXX
# 3) Regional formatted numbers with spaces/hyphens/dots (10..15 digits)
_INTL_LENIENT_RE = re.compile(
    r"(?<![A-Za-z0-9.$€£¥₹-])"
    r"([+#]\d{1,3}(?:[ .-]?\(\d{1,4}\)|[ .-]?\d{1,5}){2,5})"
    r"(?![A-Za-z0-9])"
)

_NANP_RE = re.compile(
    r"(?<![A-Za-z0-9.$€£¥₹+-])"
    r"((?:[+#]?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4})"
    r"(?![A-Za-z0-9])"
)

_REGIONAL_RE = re.compile(
    r"(?<![A-Za-z0-9.$€£¥₹+-])"
    r"(0\d{1,4}[ .-]?\d{2,4}[ .-]?\d{2,4}(?:[ .-]?\d{2,4})?)"
    r"(?![A-Za-z0-9])"
)

_DATE_LIKE_RE = re.compile(
    r"^(?:\d{4}[-./ ]\d{1,2}[-./ ]\d{1,2}|\d{1,2}[-./ ]\d{1,2}[-./ ]\d{2,4})$"
)
_SSN_SHAPE_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
_CARD_SHAPE_RE = re.compile(r"^(?:\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{5})$")
_CURRENCY_PREFIX_RE = re.compile(r"(?:[$€£¥₹]|(?:\b(?:USD|EUR|GBP|INR|PLN|SGD|SEK|HKD)\s*))$", re.I)
_CURRENCY_SUFFIX_RE = re.compile(
    r"^(?:[.,]\d{2}(?!\d)(?:\s*(?:[$€£¥₹]|(?:USD|EUR|GBP|INR|PLN|SGD|SEK|HKD)\b))?"
    r"|\s*(?:[$€£¥₹]|(?:USD|EUR|GBP|INR|PLN|SGD|SEK|HKD)\b))",
    re.I,
)
_PHONE_CUE_PREFIX_RE = re.compile(
    r"\b(?:call|tel|telephone|phone|mobile|cell|fax|ring)(?:\s*(?:no\.?|number|#|:|-))?\s*$",
    re.I,
)
_BARE_CUED_DIGITS_RE = re.compile(r"(?<![A-Za-z0-9-])(\d{10,11})(?![A-Za-z0-9-])")


def has_phone_cue_before(text: str, start: int, window: int = 12) -> bool:
    """Return True if a phone cue word ('call', 'tel', 'phone', 'mobile', 'fax', 'ring')
    appears within `window` characters before `start`."""
    prefix = text[max(0, start - window - 6):start]
    m = _PHONE_CUE_PREFIX_RE.search(prefix)
    if not m:
        return False
    return (len(prefix) - m.start()) <= (window + len(m.group().strip()))


def _is_false_positive_context(text: str, start: int, end: int, matched: str, digits: str) -> bool:
    if _DATE_LIKE_RE.match(matched):
        return True
    if _SSN_SHAPE_RE.match(matched):
        return True
    if _CARD_SHAPE_RE.match(matched):
        return True
    # Check preceding characters for currency symbols or ID prefixes like INC- / ORD- / EMP-
    prefix = text[max(0, start - 8):start]
    if _CURRENCY_PREFIX_RE.search(prefix):
        return True
    if re.search(r"[A-Za-z]{2,6}[-#]$", prefix):
        return True
    # Check trailing characters for decimal fractions (,89 / .00) and currency codes (PLN, USD, EUR) (R-02)
    suffix = text[end:min(len(text), end + 12)]
    if _CURRENCY_SUFFIX_RE.search(suffix):
        return True
    # Decimal money format like 1,204.00
    if "," in matched and "." in matched:
        return True
    # Bare digit runs without any formatting (+, #, parentheses, spaces/dashes/dots)
    # require an explicit phone cue word nearby (R-01).
    has_struct = (
        matched.startswith(("+", "#"))
        or "(" in matched
        or any(sep in matched for sep in (" ", "-", "."))
    )
    if not has_struct and not has_phone_cue_before(text, start, window=12):
        return True
    return False


def phones(text: str, regions: list[str] | None = None, cfg: Any = None) -> list[Candidate]:
    """Find phone candidates in `text` across configured regions + lenient fallback."""
    if not text:
        return []
    resolved_cfg = load_config(cfg)
    active_regions = regions if regions is not None else resolved_cfg.detect.phone_regions

    raw_candidates: list[Candidate] = []

    # 1. Optional phonenumbers library pass if installed
    try:
        import phonenumbers  # type: ignore

        for region in active_regions:
            for match in phonenumbers.PhoneNumberMatcher(
                text, region, leniency=phonenumbers.Leniency.POSSIBLE
            ):
                s, e = match.start, match.end
                # Trim unbalanced outer parentheses (e.g. '(+44 20 7946 0958')
                while s < e and text[s] == "(" and (")" not in text[s:e] or text[s + 1:e].startswith("+")):
                    s += 1
                while e > s and text[e - 1] == ")" and "(" not in text[s:e - 1]:
                    e -= 1
                matched = text[s:e]
                digits = re.sub(r"\D", "", matched)
                if not (8 <= len(digits) <= 15):
                    continue
                if _is_false_positive_context(text, s, e, matched, digits):
                    continue
                is_valid = phonenumbers.is_valid_number(match.number)
                score = 0.90 if is_valid else 0.70
                validator = "phonenumbers:valid" if is_valid else "phonenumbers:possible"
                raw_candidates.append(
                    Candidate(
                        start=s,
                        end=e,
                        pii_type=PIIType.PHONE,
                        entity_type="PHONE_NUMBER",
                        score=score,
                        validator=validator,
                        detector="phonenumbers:phone",
                        category="CONTACT",
                        severity="MEDIUM",
                        validated=is_valid,
                    )
                )
    except ImportError:
        pass

    # 2. Structured NANP pass
    for m in _NANP_RE.finditer(text):
        s, e = m.span(1)
        matched = text[s:e]
        digits = re.sub(r"\D", "", matched)
        if len(digits) not in (10, 11):
            continue
        if _is_false_positive_context(text, s, e, matched, digits):
            continue
        is_ocr = matched.startswith("#")
        structured = matched.startswith(("+", "#")) or "(" in matched or re.search(r"\d[ .-]\d", matched)
        score = 0.85 if structured and not is_ocr else (0.75 if is_ocr else 0.60)
        validator = "nanp:structured" if structured else "nanp:digits"
        raw_candidates.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.PHONE,
                entity_type="PHONE_NUMBER",
                score=score,
                validator=validator,
                detector="regex:phone",
                category="CONTACT",
                severity="MEDIUM",
                validated=bool(structured),
            )
        )

    # 3. International / lenient fallback (>= 8 digits with '+' or OCR '#' prefix)
    for m in _INTL_LENIENT_RE.finditer(text):
        s, e = m.span(1)
        matched = text[s:e]
        digits = re.sub(r"\D", "", matched)
        if not (8 <= len(digits) <= 15):
            continue
        if _is_false_positive_context(text, s, e, matched, digits):
            continue
        is_lenient = len(digits) < 10 or matched.startswith("#")
        score = 0.65 if is_lenient else 0.85
        validator = "phone:lenient_fallback" if is_lenient else "phone:intl_format"
        raw_candidates.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.PHONE,
                entity_type="PHONE_NUMBER",
                score=score,
                validator=validator,
                detector="regex:phone_lenient" if is_lenient else "regex:phone",
                category="CONTACT",
                severity="MEDIUM",
                validated=True,
            )
        )

    # 4. Regional domestic numbers (e.g. UK/FR/PL/IN leading-0 or grouped formats with 10..12 digits)
    for m in _REGIONAL_RE.finditer(text):
        s, e = m.span(1)
        matched = text[s:e]
        digits = re.sub(r"\D", "", matched)
        if not (9 <= len(digits) <= 12):
            continue
        if _is_false_positive_context(text, s, e, matched, digits):
            continue
        raw_candidates.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.PHONE,
                entity_type="PHONE_NUMBER",
                score=0.75,
                validator="phone:regional_format",
                detector="regex:phone",
                category="CONTACT",
                severity="MEDIUM",
                validated=True,
            )
        )

    # 5. Bare 10-11 digit numbers when preceded within ~12 chars by a phone cue word (R-01 / R-04)
    for m in _BARE_CUED_DIGITS_RE.finditer(text):
        s, e = m.span(1)
        matched = text[s:e]
        if not has_phone_cue_before(text, s, window=12):
            continue
        if _is_false_positive_context(text, s, e, matched, matched):
            continue
        raw_candidates.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.PHONE,
                entity_type="PHONE_NUMBER",
                score=0.80,
                validator="phone:cued_digits",
                detector="regex:phone",
                category="CONTACT",
                severity="MEDIUM",
                validated=True,
            )
        )

    # Deduplicate overlapping spans: prefer longest span, then highest score, then earliest start
    raw_candidates.sort(key=lambda c: (-(c.end - c.start), -c.score, c.start, c.detector))
    kept: list[Candidate] = []
    taken: list[tuple[int, int]] = []
    for cand in raw_candidates:
        if any(cand.start < te and ts < cand.end for ts, te in taken):
            continue
        taken.append((cand.start, cand.end))
        kept.append(cand)

    kept.sort(key=lambda c: (c.start, -(c.end - c.start)))
    return kept


recognize_phones = phones

