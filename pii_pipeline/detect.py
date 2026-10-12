"""PII detection over extracted text. Owner: builder.

detect() is the Member 3 engine (detect/engine.py: patterns + validators, context, names,
per-document name registry, allow-list with reason codes) plus this module's original
regex detector as a safety net: a pattern finding is kept only where the engine has not
already decided the span (masked it, sent it to review, or allow-listed it with a reason).
The net keeps the fail-closed rule that unclaimed 7+ digit runs go to REVIEW.
detect_residual() is the stricter re-scan for the release gate.
Findings hold locations, never matched values.
"""
import ipaddress
import re

from detect.config import load_config
from detect.engine import detect_and_resolve, detect_strict

from .models import Action, ExtractedDocument, Finding, PIIType

_EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_CARD = re.compile(  # contiguous, 4-4-4-(1..7) or Amex 4-6-5 with one consistent separator
    r"(?<![\d-])(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{5})(?![\d-]?\d)")
_SSN = re.compile(r"(?<![\d-])(\d{3})-(\d{2})-(\d{4})(?![\d-]?\d)")
_SSN_CTX = re.compile(r"(?:ssn|social security(?: number| no\.?)?)\W{0,3}(\d{9})(?!\d)", re.I)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f:]{3,39}(?![\w:])")
# Bounded shapes only (one NANP number, or "+" international); never a free-running digit chain,
# so two adjacent numbers cannot be swallowed as one. Leftover digit runs go to REVIEW.
_PHONE = re.compile(
    r"(?<![\w.])(?:(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}"
    r"|\+\d{1,3}(?:[ .-]?\(?\d{1,4}\)?){2,5})(?!\w)")
_DIGIT_RUN = re.compile(r"(?<![\w.])\d(?:[ .-]?\d){6,}(?!\w)")
_DATE = re.compile(r"\d{4}[-.]\d{1,2}[-.]\d{1,2}|\d{1,2}[-.]\d{1,2}[-.]\d{4}")

# Higher wins on overlap. Ties: longer span, then earlier start, then type name.
_PRIORITY = {PIIType.EMAIL: 5, PIIType.CREDIT_CARD: 4, PIIType.US_SSN: 3, PIIType.IP_ADDRESS: 2, PIIType.PHONE: 1}


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _candidates(text: str):
    """Yield (type, start, end, detector, confidence, action, rank_boost)."""
    for m in _EMAIL.finditer(text):
        yield PIIType.EMAIL, m.start(), m.end(), "regex:email", 0.95, Action.MASK, 1

    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if not 13 <= len(digits) <= 19:
            continue
        if _luhn(digits):
            yield PIIType.CREDIT_CARD, m.start(), m.end(), "luhn:credit_card", 0.9, Action.MASK, 1
        elif re.fullmatch(r"\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}", m.group()):
            # card-shaped but fails Luhn: could be a typo or a look-alike ID
            yield PIIType.CREDIT_CARD, m.start(), m.end(), "regex:credit_card_no_luhn", 0.4, Action.REVIEW, 0

    for m in _SSN.finditer(text):
        area, group, serial = m.groups()
        if area in ("000", "666") or area[0] == "9" or group == "00" or serial == "0000":
            continue    # not issuable as an SSN
        yield PIIType.US_SSN, m.start(), m.end(), "regex:us_ssn", 0.85, Action.MASK, 1
    for m in _SSN_CTX.finditer(text):
        yield PIIType.US_SSN, m.start(1), m.end(1), "regex:us_ssn_context", 0.7, Action.MASK, 0

    for m in _IPV4.finditer(text):
        octets = m.group().split(".")
        if all(int(o) <= 255 and not (len(o) > 1 and o[0] == "0") for o in octets):
            yield PIIType.IP_ADDRESS, m.start(), m.end(), "regex:ipv4", 0.8, Action.MASK, 1
    for m in _IPV6.finditer(text):
        s = m.group()
        if s.count(":") >= 2 and re.search(r"[0-9A-Fa-f]", s):
            try:
                ipaddress.IPv6Address(s)
            except ValueError:
                continue
            yield PIIType.IP_ADDRESS, m.start(), m.end(), "regex:ipv6", 0.8, Action.MASK, 1

    for m in _PHONE.finditer(text):
        s = m.group()
        digits = re.sub(r"\D", "", s)
        if not 10 <= len(digits) <= 15:
            continue
        if re.fullmatch(r"\d{4}[-./ ]\d{1,2}[-./ ]\d{1,2}", s):   # ISO-ish date
            continue
        structured = s.startswith("+") or "(" in s or re.search(r"\d[ .-]\d", s)
        if structured:
            yield PIIType.PHONE, m.start(), m.end(), "regex:phone", 0.8, Action.MASK, 1
        else:   # bare digit run: could be an ID, account or order number
            yield PIIType.PHONE, m.start(), m.end(), "regex:phone_bare_digits", 0.4, Action.REVIEW, 0


def pattern_detect(doc: ExtractedDocument) -> list[Finding]:
    """Original regex detector. Findings have char spans into TextUnit.text; low-confidence hits
    -> Action.REVIEW."""
    findings: list[Finding] = []
    for unit in doc.units:
        cands = list(_candidates(unit.text))
        cands.sort(key=lambda c: (-_PRIORITY[c[0]], -c[6], -(c[2] - c[1]), c[1], c[0].value))
        taken: list[tuple[int, int]] = []
        kept = []
        for c in cands:
            if any(c[1] < e and s < c[2] for s, e in taken):
                continue
            taken.append((c[1], c[2]))
            kept.append(c)
        for typ, s, e, det, conf, act, _ in sorted(kept, key=lambda c: c[1]):
            findings.append(Finding(typ, unit.unit_id, s, e, det, conf, act))
        # Digit runs not claimed by any detector (e.g. a phone fragment) are never silently passed.
        masked = list(unit.text)
        for s, e in taken:
            masked[s:e] = "\x00" * (e - s)
        for m in _DIGIT_RUN.finditer("".join(masked)):
            if not _DATE.fullmatch(m.group()):
                findings.append(Finding(PIIType.OTHER, unit.unit_id, m.start(), m.end(),
                                        "regex:unclaimed_digit_run", 0.3, Action.REVIEW))
    return findings


def _settles(f: Finding) -> bool:
    """An engine finding that decides its span: mask/redact/review, or an allow-list suppression.
    A plain below-threshold KEEP decides nothing, so the safety net still applies there."""
    return f.action is not Action.KEEP or getattr(f, "reason", "").startswith("ALLOW_")


def _with_safety_net(doc: ExtractedDocument, engine: list[Finding]) -> list[Finding]:
    settled: dict[str, list[tuple[int, int]]] = {}
    for f in engine:
        if _settles(f) and f.start is not None:
            settled.setdefault(f.unit_id, []).append((f.start, f.end))
    net = [p for p in pattern_detect(doc)
           if not any(p.start < e and s < p.end for s, e in settled.get(p.unit_id, ()))]
    return engine + net


def detect(doc: ExtractedDocument) -> list[Finding]:
    """Engine findings (including ALLOW records as Action.KEEP with a reason code) plus the
    pattern safety net. Char spans index TextUnit.text."""
    return _with_safety_net(doc, detect_and_resolve(doc))


def detect_residual(doc: ExtractedDocument) -> list[Finding]:
    """Stricter re-scan of redacted output for the gate: the engine's strict mode per unit (lower
    threshold, no allow-list for HIGH types, placeholder tags ignored) plus the safety net."""
    cfg = load_config()
    engine: list[Finding] = []
    for unit in doc.units:
        engine += detect_strict(unit.text, cfg, doc_id=doc.source.doc_id, block_id=unit.unit_id,
                                include_allow=True)
    return _with_safety_net(doc, engine)

