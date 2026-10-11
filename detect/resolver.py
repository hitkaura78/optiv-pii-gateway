"""Candidate resolver, overlap merger, and allow-list precedence engine (Task T2.2, T2.3).

Responsibilities:
1. Merge overlapping candidates deterministically:
   - Exact same (start, end) span: merge contributing detector names into `detectors`.
   - Overlapping spans: longest span wins; ties broken by explicit priority:
     GOV_ID > DOB > ADDRESS > EMAIL > PHONE > ID > FRAGMENT > PERSON.
   - Raw regex vs NER scores are NOT compared as calibrated probabilities; explicit
     priority and validation status govern conflict resolution.
2. Apply allow-list rules with explicit reason codes (ALLOW_REF_ID_*, ALLOW_GENERIC_MAILBOX,
   ALLOW_COMPANY_NAME, ALLOW_HQ_ADDRESS, ALLOW_HOTLINE_NUMBER, ALLOW_MONEY_AMOUNT).
3. Allow-list precedence rule:
   - Never silently override a validated HIGH-severity structured PII signal (e.g. US_SSN,
     IN_PAN, PL_PESEL, CREDIT_CARD, PASSPORT); conflict -> Action.REVIEW with
     reason="ALLOWLIST_CONFLICT".
   - In strict mode (`strict=True`), allow-list suppression is disabled for all HIGH-severity types.
4. Low-OCR confidence handling:
   - Units with `ocr_confidence < low_ocr_confidence_threshold` (0.70) receive Action.MASK
     with reason marker "LOW_OCR_CONFIDENCE".
5. Assign deterministic `entity_key` and `entity_type_tag` (e.g. [EMAIL_001], [PERSON_001]).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from detect.config import DetectorConfig, load_config
from detect.models import Candidate, DetectedFinding
from pii_pipeline.models import Action, PIIType

# Explicit priority tiers (higher number = higher priority on equal-length span ties)
PRIORITY_RANK: dict[str, int] = {
    "GOVERNMENT_ID": 80,
    "FINANCIAL": 75,
    "DOB": 70,
    "HOME_ADDRESS": 65,
    "CONTACT_EMAIL": 60,
    "CONTACT_PHONE": 50,
    "EMPLOYEE_IDENTIFIER": 40,
    "FINANCIAL_FRAGMENT": 30,
    "FAMILY_MEMBER": 25,
    "PERSON": 20,
}

# Validated structured HIGH-severity types that must never be silently overridden by allow-list
VALIDATED_HIGH_ENTITY_TYPES = {
    "US_SSN",
    "IN_PAN",
    "PL_PESEL",
    "CREDIT_CARD",
    "PASSPORT",
}


def _priority_for_candidate(c: Candidate) -> int:
    if (
        c.category == "GOVERNMENT_ID"
        or c.pii_type in (PIIType.US_SSN, PIIType.PAN)
        or c.entity_type in ("US_SSN", "IN_PAN", "PL_PESEL", "PL_PESEL_MASKED", "PASSPORT")
    ):
        return PRIORITY_RANK["GOVERNMENT_ID"]
    if c.category == "FINANCIAL" or c.pii_type is PIIType.CREDIT_CARD or c.entity_type == "CREDIT_CARD":
        return PRIORITY_RANK["FINANCIAL"]
    if c.pii_type is PIIType.DATE_OF_BIRTH or c.category == "DOB" or c.entity_type == "DATE_OF_BIRTH":
        return PRIORITY_RANK["DOB"]
    if c.pii_type is PIIType.ADDRESS or c.category == "HOME_ADDRESS" or c.entity_type == "HOME_ADDRESS":
        return PRIORITY_RANK["HOME_ADDRESS"]
    if c.pii_type is PIIType.EMAIL or c.entity_type == "EMAIL_ADDRESS":
        return PRIORITY_RANK["CONTACT_EMAIL"]
    if c.pii_type is PIIType.PHONE or c.entity_type == "PHONE_NUMBER":
        return PRIORITY_RANK["CONTACT_PHONE"]
    if c.category == "EMPLOYEE_IDENTIFIER" or c.entity_type in ("EMPLOYEE_ID", "DIRECTOR_ID", "VENDOR_WORKER_ID"):
        return PRIORITY_RANK["EMPLOYEE_IDENTIFIER"]
    if c.category == "FINANCIAL_FRAGMENT" or c.entity_type in ("CARD_LAST4", "ACCOUNT_LAST4", "SSN_LAST4"):
        return PRIORITY_RANK["FINANCIAL_FRAGMENT"]
    if c.category == "FAMILY_MEMBER" or c.entity_type == "FAMILY_MEMBER_NAME":
        return PRIORITY_RANK["FAMILY_MEMBER"]
    return PRIORITY_RANK["PERSON"]


@dataclass(frozen=True)
class AllowlistMatch:
    start: int
    end: int
    reason: str
    kind: str  # "ref_id" | "generic_mailbox" | "company_name" | "hq_address" | "hotline" | "money_amount"
    pii_type: PIIType = PIIType.OTHER


def find_allowlist_spans(text: str, cfg: Any = None) -> list[AllowlistMatch]:
    """Scan `text` for configured allow-list items and return their spans + reason codes."""
    if not text:
        return []
    resolved_cfg: DetectorConfig = load_config(cfg)
    al = resolved_cfg.allowlist
    matches: list[AllowlistMatch] = []

    # 1. Reference ID prefixes (INC-, LOS-, RSK-, CTL-, GRP-, SPK-, ISS-)
    for prefix, reason in sorted(al.reference_id_prefixes.items()):
        if not prefix:
            continue
        pat = re.compile(rf"(?<![A-Za-z0-9-])({re.escape(prefix)}[A-Za-z0-9-]+)(?![A-Za-z0-9-])")
        for m in pat.finditer(text):
            s, e = m.span(1)
            matches.append(AllowlistMatch(s, e, str(reason), "ref_id", PIIType.OTHER))

    # 2. Generic mailboxes
    for entry in al.generic_mailboxes:
        pat_str = entry.get("pattern", "") if isinstance(entry, dict) else ""
        reason = entry.get("reason", "ALLOW_GENERIC_MAILBOX") if isinstance(entry, dict) else "ALLOW_GENERIC_MAILBOX"
        if not pat_str:
            continue
        inner = pat_str.lstrip("^").rstrip("$")
        pat = re.compile(rf"(?<![A-Za-z0-9._%+-])({inner})(?![A-Za-z0-9_-])", re.I)
        for m in pat.finditer(text):
            s, e = m.span(1)
            matches.append(AllowlistMatch(s, e, str(reason), "generic_mailbox", PIIType.EMAIL))

    # 3. Company names
    for entry in al.company_names:
        val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        reason = entry.get("reason", "ALLOW_COMPANY_NAME") if isinstance(entry, dict) else "ALLOW_COMPANY_NAME"
        if not val.strip():
            continue
        pat = re.compile(rf"(?<![A-Za-z0-9_-])({re.escape(val.strip())})(?![A-Za-z0-9_-])", re.I)
        for m in pat.finditer(text):
            s, e = m.span(1)
            matches.append(AllowlistMatch(s, e, str(reason), "company_name", PIIType.PERSON))

    # 4. HQ addresses
    for entry in al.hq_addresses:
        val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        reason = entry.get("reason", "ALLOW_HQ_ADDRESS") if isinstance(entry, dict) else "ALLOW_HQ_ADDRESS"
        if not val.strip():
            continue
        pat = re.compile(rf"(?<![A-Za-z0-9])({re.escape(val.strip())})(?![A-Za-z0-9])", re.I)
        for m in pat.finditer(text):
            s, e = m.span(1)
            matches.append(AllowlistMatch(s, e, str(reason), "hq_address", PIIType.ADDRESS))

    # 5. Hotlines
    for entry in al.hotlines:
        val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        reason = entry.get("reason", "ALLOW_HOTLINE_NUMBER") if isinstance(entry, dict) else "ALLOW_HOTLINE_NUMBER"
        if not val.strip():
            continue
        pat = re.compile(rf"(?<![A-Za-z0-9])({re.escape(val.strip())})(?![A-Za-z0-9])", re.I)
        for m in pat.finditer(text):
            s, e = m.span(1)
            matches.append(AllowlistMatch(s, e, str(reason), "hotline", PIIType.PHONE))

    # 6. Money amounts
    for entry in al.money_amounts:
        pat_str = entry.get("pattern", "") if isinstance(entry, dict) else ""
        reason = entry.get("reason", "ALLOW_MONEY_AMOUNT") if isinstance(entry, dict) else "ALLOW_MONEY_AMOUNT"
        if not pat_str:
            continue
        pat = re.compile(rf"({pat_str})")
        for m in pat.finditer(text):
            s, e = m.span(1)
            matches.append(AllowlistMatch(s, e, str(reason), "money_amount", PIIType.OTHER))

    matches.sort(key=lambda m: (m.start, -(m.end - m.start), m.reason))
    # Deduplicate overlapping allow-list spans (keep longest)
    deduped: list[AllowlistMatch] = []
    taken: list[tuple[int, int]] = []
    for m in matches:
        if any(m.start < te and ts < m.end for ts, te in taken):
            continue
        taken.append((m.start, m.end))
        deduped.append(m)
    return deduped


def _deterministic_finding_id(doc_id: str, unit_id: str, start: int, end: int, entity_type: str, action: str) -> str:
    payload = f"{doc_id}|{unit_id}|{start}:{end}|{entity_type}|{action}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class EntityTagAssigner:
    """Maintains deterministic per-document entity keys and typed tags (e.g. [EMAIL_001]).

    Rule: `entity_key` is the structured employee/director/vendor ID when known
    (e.g. 'EMP-20415'), or a deterministic hash (`ek_<sha256[:12]>`) so raw PII values
    are never stored in Finding fields.
    """

    _STRUCTURED_ID_KEY_RE = re.compile(r"^[A-Z]{2,6}-(?:[A-Z]{2,3}-)?\d{3,8}$")

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._assigned_tags: dict[tuple[str, str], str] = {}

    def assign(self, entity_type: str, raw_key: str) -> tuple[str, str]:
        norm_type = entity_type.upper()
        cleaned = " ".join(raw_key.strip().split())
        norm_key = cleaned.lower()
        lookup = (norm_type, norm_key)
        if self._STRUCTURED_ID_KEY_RE.match(cleaned):
            safe_key = cleaned
        else:
            safe_key = f"ek_{hashlib.sha256(norm_key.encode('utf-8')).hexdigest()[:12]}"
        if lookup in self._assigned_tags:
            return safe_key, self._assigned_tags[lookup]
        idx = self._counters.get(norm_type, 0) + 1
        self._counters[norm_type] = idx
        tag = f"[{norm_type}_{idx:03d}]"
        self._assigned_tags[lookup] = tag
        return safe_key, tag


def resolve_unit_candidates(
    unit_id: str,
    text: str,
    candidates: list[Candidate],
    cfg: Any = None,
    *,
    doc_id: str = "",
    ocr_confidence: float | None = None,
    strict: bool = False,
    include_allow: bool = True,
    tag_assigner: EntityTagAssigner | None = None,
) -> list[DetectedFinding]:
    """Resolve overlapping candidates and allow-list rules for one text unit deterministically."""
    resolved_cfg = load_config(cfg)
    threshold = resolved_cfg.detect.strict_threshold if strict else resolved_cfg.detect.mask_threshold
    low_ocr = (
        ocr_confidence is not None
        and ocr_confidence < resolved_cfg.detect.low_ocr_confidence_threshold
    )
    assigner = tag_assigner if tag_assigner is not None else EntityTagAssigner()

    # 1. Group candidates with identical (start, end) to merge detector names
    by_span: dict[tuple[int, int], list[Candidate]] = {}
    for c in candidates:
        if c.start < 0 or c.end > len(text) or c.start >= c.end:
            continue
        by_span.setdefault((c.start, c.end), []).append(c)

    merged_candidates: list[ tuple[Candidate, tuple[str, ...]] ] = []
    for span, group in sorted(by_span.items()):
        # Sort within same span by priority, validated flag, score, entity_type, detector
        group_sorted = sorted(
            group,
            key=lambda x: (
                -_priority_for_candidate(x),
                -int(x.validated),
                -x.score,
                x.entity_type,
                x.detector,
            ),
        )
        winner = group_sorted[0]
        # Prefer a structured ID entity_key (e.g. EMP-20415 linked by NameRegistry) if any candidate in the group has one
        linked_id_key = next(
            (
                x.entity_key
                for x in group_sorted
                if x.entity_key and EntityTagAssigner._STRUCTURED_ID_KEY_RE.match(x.entity_key)
            ),
            winner.entity_key,
        )
        if linked_id_key != winner.entity_key:
            winner = Candidate(
                start=winner.start,
                end=winner.end,
                pii_type=winner.pii_type,
                entity_type=winner.entity_type,
                score=winner.score,
                validator=winner.validator,
                detector=winner.detector,
                category=winner.category,
                severity=winner.severity,
                reason=winner.reason,
                entity_key=linked_id_key,
                validated=winner.validated,
            )
        detectors = tuple(dict.fromkeys(x.detector for x in group_sorted))
        merged_candidates.append((winner, detectors))

    # 2. Sort merged candidates for overlap resolution:
    # Longest span first, then priority tier, then validated flag, then score, then start offset
    merged_candidates.sort(
        key=lambda item: (
            -(item[0].end - item[0].start),
            -_priority_for_candidate(item[0]),
            -int(item[0].validated),
            -item[0].score,
            item[0].start,
            item[0].entity_type,
        )
    )

    selected: list[tuple[Candidate, tuple[str, ...]]] = []
    taken_spans: list[tuple[int, int]] = []
    for cand, dets in merged_candidates:
        if any(cand.start < te and ts < cand.end for ts, te in taken_spans):
            continue
        taken_spans.append((cand.start, cand.end))
        selected.append((cand, dets))

    # 3. Evaluate allow-list spans and precedence rules
    allow_spans = find_allowlist_spans(text, resolved_cfg)
    findings: list[DetectedFinding] = []
    used_allow_indices: set[int] = set()

    for cand, dets in sorted(selected, key=lambda item: (item[0].start, item[0].end, item[0].entity_type)):
        overlapping_allows: list[tuple[int, AllowlistMatch]] = [
            (idx, al_m)
            for idx, al_m in enumerate(allow_spans)
            if cand.start < al_m.end and al_m.start < cand.end
        ]

        # Check allow-list interaction
        if overlapping_allows:
            idx0, al_match = overlapping_allows[0]
            for idx, _ in overlapping_allows:
                used_allow_indices.add(idx)

            is_high_severity = cand.severity.upper() == "HIGH"
            is_explicit_hq_suppression = (
                cand.entity_type == "HOME_ADDRESS" and al_match.kind == "hq_address"
            )
            is_validated_high_id = (
                cand.validated
                and not is_explicit_hq_suppression
                and (is_high_severity or cand.entity_type in VALIDATED_HIGH_ENTITY_TYPES)
            )

            if strict and is_high_severity:
                # Strict mode rule: never suppress HIGH-severity types via allow-list
                pass
            elif is_validated_high_id:
                # Precedence rule: a validated HIGH-severity structured PII signal
                # (e.g. US_SSN, IN_PAN, PL_PESEL, CREDIT_CARD, PASSPORT) conflicting with
                # an allow-list rule must NOT be silently allowed -> emit REVIEW!
                surface = text[cand.start:cand.end]
                ekey, etag = assigner.assign(cand.entity_type, cand.entity_key or surface)
                fid = _deterministic_finding_id(doc_id, unit_id, cand.start, cand.end, cand.entity_type, "review")
                findings.append(
                    DetectedFinding(
                        pii_type=cand.pii_type,
                        unit_id=unit_id,
                        start=cand.start,
                        end=cand.end,
                        detector=cand.detector,
                        confidence=cand.score,
                        action=Action.REVIEW,
                        finding_id=fid,
                        detectors=dets,
                        validator=cand.validator,
                        reason="ALLOWLIST_CONFLICT",
                        entity_key=ekey,
                        entity_type_tag=etag,
                        category=cand.category,
                        severity=cand.severity,
                        doc_id=doc_id,
                    )
                )
                continue
            else:
                # Context supports allow-list suppression (e.g. generic mailbox, HQ address,
                # hotline phone, company name, or reference ID) -> emit ALLOW (Action.KEEP)
                if include_allow:
                    fid = _deterministic_finding_id(
                        doc_id, unit_id, cand.start, cand.end, cand.entity_type, "allow"
                    )
                    findings.append(
                        DetectedFinding(
                            pii_type=cand.pii_type,
                            unit_id=unit_id,
                            start=cand.start,
                            end=cand.end,
                            detector=cand.detector,
                            confidence=cand.score,
                            action=Action.KEEP,
                            finding_id=fid,
                            detectors=dets,
                            validator=cand.validator,
                            reason=al_match.reason,
                            entity_key="",
                            entity_type_tag="",
                            category=cand.category,
                            severity=cand.severity,
                            doc_id=doc_id,
                        )
                    )
                continue

        # Candidate not covered by allow-list: determine MASK / REVIEW / ALLOW
        surface = text[cand.start:cand.end]
        if cand.reason == "LUHN_FAILED_REVIEW" or cand.validator.endswith(":fail"):
            action = Action.REVIEW
            reason = cand.reason or "VALIDATION_FAILED_REVIEW"
        elif cand.validated or cand.score >= threshold or low_ocr:
            action = Action.MASK
            reason = cand.reason or f"DETECTED_{cand.entity_type}"
            if low_ocr:
                reason = f"{reason}|LOW_OCR_CONFIDENCE" if reason else "LOW_OCR_CONFIDENCE"
        else:
            action = Action.KEEP
            reason = "BELOW_MASK_THRESHOLD"

        if action is Action.KEEP and not include_allow:
            continue

        ekey, etag = (
            assigner.assign(cand.entity_type, cand.entity_key or surface)
            if action in (Action.MASK, Action.REDACT, Action.REVIEW)
            else ("", "")
        )
        fid = _deterministic_finding_id(doc_id, unit_id, cand.start, cand.end, cand.entity_type, action.value)
        findings.append(
            DetectedFinding(
                pii_type=cand.pii_type,
                unit_id=unit_id,
                start=cand.start,
                end=cand.end,
                detector=cand.detector,
                confidence=cand.score,
                action=action,
                finding_id=fid,
                detectors=dets,
                validator=cand.validator,
                reason=reason,
                entity_key=ekey,
                entity_type_tag=etag,
                category=cand.category,
                severity=cand.severity,
                doc_id=doc_id,
            )
        )

    # 4. Emit standalone ALLOW records for allow-list matches (e.g. INC- reference IDs,
    # company names, money amounts) that did not overlap any candidate
    if include_allow:
        for idx, al_m in enumerate(allow_spans):
            if idx in used_allow_indices:
                continue
            if any(al_m.start < f.end and f.start < al_m.end for f in findings if f.start is not None and f.end is not None):
                continue
            fid = _deterministic_finding_id(doc_id, unit_id, al_m.start, al_m.end, al_m.kind, "allow")
            findings.append(
                DetectedFinding(
                    pii_type=al_m.pii_type,
                    unit_id=unit_id,
                    start=al_m.start,
                    end=al_m.end,
                    detector=f"allowlist:{al_m.kind}",
                    confidence=1.0,
                    action=Action.KEEP,
                    finding_id=fid,
                    detectors=(f"allowlist:{al_m.kind}",),
                    validator=f"allowlist:{al_m.kind}",
                    reason=al_m.reason,
                    entity_key="",
                    entity_type_tag="",
                    category="ALLOWLIST",
                    severity="LOW",
                    doc_id=doc_id,
                )
            )

    findings.sort(key=lambda f: (f.start if f.start is not None else -1, f.end if f.end is not None else -1, f.pii_type.value))
    return findings
