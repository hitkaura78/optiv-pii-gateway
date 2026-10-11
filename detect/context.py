"""Layer 2 context rules and table column typing (Tasks T2.1, T3.2 / U-11, U-12, H11).

Responsibilities:
- Coordinate DOB (<=25 chars cue), last-four (<=30 chars cue), and home-address recognizers.
- Detect family-member context cues ('husband', 'wife', 'spouse', 'son', 'daughter', etc.)
  to reclassify adjacent PERSON candidates as FAMILY_MEMBER_NAME.
- Table column typing using table_id/row/col locators and YAML-configured header labels
  (EMP ID, NATIONAL ID, MOBILE, E-MAIL, RACI headers), while scanning header cells themselves.
"""
from __future__ import annotations

import re
from typing import Any

from detect.config import load_config
from detect.models import Candidate
from detect.recognizers.address import recognize_addresses
from detect.recognizers.dob import recognize_dob
from detect.recognizers.fragments import recognize_fragments
from pii_pipeline.models import PIIType

_FAMILY_CUE_RE = re.compile(
    r"\b(?:husband|wife|spouse|son|daughter|mother|father|brother|sister|next\s+of\s+kin|emergency\s+contact)\b",
    re.I,
)

# Matches both DOCX ("table:0/r1/c2...") and PPTX ("slide:1/shape:1/table/r0/c0...")
_TABLE_UNIT_RE = re.compile(
    r"(?:(?P<prefix>slide:\d+/shape:\d+/)?table(?::(?P<tid>\d+))?)/r(?P<row>\d+)/c(?P<col>\d+)"
)

_CELL_NAME_RE = re.compile(
    r"^\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)+)\s*$"
)


def recognize_context(text: str, cfg: Any = None) -> list[Candidate]:
    """Run all L2 context recognizers (DOB, financial/SSN fragments, home addresses)."""
    if not text:
        return []
    out: list[Candidate] = []
    out.extend(recognize_dob(text, cfg))
    out.extend(recognize_fragments(text, cfg))
    out.extend(recognize_addresses(text))
    out.sort(key=lambda c: (c.start, -(c.end - c.start), c.entity_type))
    return out


def find_family_cues(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of family relationship cue words in `text`."""
    if not text:
        return []
    return [m.span() for m in _FAMILY_CUE_RE.finditer(text)]


def upgrade_family_Context(candidates: list[Candidate], text: str, cfg: Any = None) -> list[Candidate]:
    """Upgrade PERSON candidates preceded within `family_context_window_chars` by a family cue."""
    cues = find_family_cues(text)
    if not cues or not candidates:
        return candidates
    resolved_cfg = load_config(cfg)
    window = resolved_cfg.detect.family_context_window_chars
    upgraded: list[Candidate] = []
    for c in candidates:
        if c.pii_type is PIIType.PERSON:
            has_family_cue = any(
                cue_end <= c.start and (c.start - cue_end) <= window
                for _, cue_end in cues
            )
            if has_family_cue:
                upgraded.append(
                    Candidate(
                        start=c.start,
                        end=c.end,
                        pii_type=PIIType.PERSON,
                        entity_type="FAMILY_MEMBER_NAME",
                        score=min(0.95, c.score + 0.10),
                        validator="context:family_cue",
                        detector=c.detector,
                        category="FAMILY_MEMBER",
                        severity="MEDIUM",
                        entity_key=c.entity_key,
                        validated=True,
                    )
                )
                continue
        upgraded.append(c)
    return upgraded


def parse_table_locator(unit: Any) -> tuple[str, int, int] | None:
    """Extract (table_id, row, col) from a unit/block locator or unit_id string."""
    locator = getattr(unit, "locator", None)
    if locator is not None:
        tid = getattr(locator, "table_id", None)
        row = getattr(locator, "row", None)
        col = getattr(locator, "col", None)
        if tid is not None and row is not None and col is not None:
            return str(tid), int(row), int(col)
    uid = getattr(unit, "unit_id", None) or getattr(unit, "block_id", None) or ""
    m = _TABLE_UNIT_RE.search(str(uid))
    if not m:
        return None
    tid = (m.group("prefix") or "") + (m.group("tid") or "0")
    return tid, int(m.group("row")), int(m.group("col"))


def infer_table_column_types(units: list[Any], cfg: Any = None) -> dict[tuple[str, int], str]:
    """Map (table_id, col) -> canonical header category from row-0 header cells using YAML mappings."""
    resolved_cfg = load_config(cfg)
    header_map = resolved_cfg.table_column_headers or {}
    col_types: dict[tuple[str, int], str] = {}

    for u in units:
        loc = parse_table_locator(u)
        if loc is None:
            continue
        tid, row, col = loc
        if row != 0:
            continue
        text_upper = (getattr(u, "text", "") or "").strip().upper()
        if not text_upper:
            continue
        for canonical_type, labels in sorted(header_map.items()):
            for lbl in labels:
                lbl_upper = lbl.strip().upper()
                if not lbl_upper:
                    continue
                if (
                    text_upper == lbl_upper
                    or text_upper.startswith(lbl_upper + ":")
                    or text_upper.startswith(lbl_upper + " -")
                    or text_upper.startswith(lbl_upper + " (")
                    or re.search(rf"\b{re.escape(lbl_upper)}\b", text_upper)
                ):
                    col_types[(tid, col)] = canonical_type
                    break
            if (tid, col) in col_types:
                break
    return col_types


def apply_table_column_typing(
    unit: Any,
    candidates: list[Candidate],
    col_types: dict[tuple[str, int], str],
) -> list[Candidate]:
    """Boost or add typed candidates for table cells under a typed column header."""
    loc = parse_table_locator(unit)
    if loc is None:
        return candidates
    tid, row, col = loc
    col_type = col_types.get((tid, col))
    if not col_type or row == 0:
        return candidates

    text = getattr(unit, "text", "") or ""
    if not text.strip():
        return candidates

    updated: list[Candidate] = []
    matched_col_type = False
    for c in candidates:
        if (
            (col_type == "PERSON" and c.pii_type is PIIType.PERSON)
            or (col_type == "EMAIL" and c.pii_type is PIIType.EMAIL)
            or (col_type == "PHONE" and c.pii_type is PIIType.PHONE)
            or (col_type in ("EMPLOYEE_ID", "DIRECTOR_ID", "VENDOR_WORKER_ID") and c.entity_type == col_type)
            or (col_type == "GOV_ID" and c.category == "GOVERNMENT_ID")
            or (col_type == "DATE_OF_BIRTH" and c.pii_type is PIIType.DATE_OF_BIRTH)
            or (col_type == "ADDRESS" and c.pii_type is PIIType.ADDRESS)
        ):
            matched_col_type = True
            updated.append(
                Candidate(
                    start=c.start,
                    end=c.end,
                    pii_type=c.pii_type,
                    entity_type=c.entity_type,
                    score=min(0.98, c.score + 0.10),
                    validator=f"{c.validator}+table_col:{col_type.lower()}",
                    detector=c.detector,
                    category=c.category,
                    severity=c.severity,
                    reason=c.reason,
                    entity_key=c.entity_key,
                    validated=True,
                )
            )
        else:
            updated.append(c)

    # If a PERSON column cell was not yet caught by NER (e.g. two-word name in a bare table cell),
    # emit a table-column-typed PERSON candidate.
    if not matched_col_type and col_type == "PERSON":
        m = _CELL_NAME_RE.match(text)
        if m:
            s, e = m.span(1)
            updated.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.PERSON,
                    entity_type="PERSON",
                    score=0.88,
                    validator="table_col:person",
                    detector="context:table_column",
                    category="PERSON",
                    severity="MEDIUM",
                    entity_key=" ".join(text[s:e].split()),
                    validated=True,
                )
            )
    return updated

