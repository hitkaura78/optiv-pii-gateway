"""Main Member 3 PII detection engine (Tasks T0.2, T2.4, T3.4).

Exposes:
- detect_and_resolve(doc, cfg=None) -> list[DetectedFinding]
- detect_strict(text, cfg=None) -> list[DetectedFinding]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from detect.config import DetectorConfig, load_config
from detect.context import (
    apply_table_column_typing,
    infer_table_column_types,
    recognize_context,
    upgrade_family_Context,
)
from detect.models import Candidate, DetectedFinding
from detect.ner import recognize_persons
from detect.recognizers import (
    recognize_emails,
    recognize_govids,
    recognize_ids,
    recognize_phones,
)
from detect.registry import NameRegistry
from detect.resolver import EntityTagAssigner, resolve_unit_candidates

# Placeholder tags produced by redaction (e.g. [EMAIL], [PERSON_003], [US_SSN])
_REDACTED_TAG_RE = re.compile(r"\[[A-Z][A-Z0-9_]{1,30}\]")
_PAGE_SLIDE_RE = re.compile(r"(?:page|slide):(\d+)")


@dataclass
class _NormalizedUnit:
    unit_id: str
    text: str
    page_num: int
    block_order: int
    ocr_confidence: float | None
    raw_unit: Any


def _mask_redacted_tags(text: str) -> str:
    """Replace placeholder tags like [PERSON_003] or [EMAIL] with spaces of equal length
    so character offsets of surrounding text remain 100% unchanged."""
    if "[" not in text:
        return text
    return _REDACTED_TAG_RE.sub(lambda m: " " * (m.end() - m.start()), text)


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_ordered_units(doc: Any) -> tuple[str, list[_NormalizedUnit]]:
    """Extract ordered text units from ExtractedDocument, IR Document object, or IR JSON dict."""
    doc_id = ""
    source = _get_field(doc, "source")
    if source is not None:
        doc_id = str(_get_field(source, "doc_id", "") or "")
    else:
        doc_id = str(_get_field(doc, "doc_id", "") or "")

    out: list[_NormalizedUnit] = []
    # Case 1: pii_pipeline.models.ExtractedDocument (.units)
    units = _get_field(doc, "units")
    if units is not None and not isinstance(units, type(dict.values)):
        for idx, u in enumerate(units):
            uid = str(_get_field(u, "unit_id") or _get_field(u, "block_id") or f"unit:{idx}")
            text = str(_get_field(u, "text", "") or "")
            ocr_conf = _get_field(u, "ocr_confidence")
            if ocr_conf is None:
                ocr_conf = _get_field(u, "ocr_conf")
            m = _PAGE_SLIDE_RE.search(uid)
            page_num = int(m.group(1)) if m else 1
            out.append(_NormalizedUnit(uid, text, page_num, idx, ocr_conf, u))
        return doc_id, out

    # Case 2: IR Document / ir_min.json dict with pages -> blocks and optional metadata_blocks
    order_counter = 0
    pages = _get_field(doc, "pages") or []
    for p_idx, page in enumerate(pages, start=1):
        p_num = int(_get_field(page, "page_num") or _get_field(page, "page_number") or p_idx)
        blocks = _get_field(page, "blocks") or []
        for b in blocks:
            bid = str(_get_field(b, "block_id") or _get_field(b, "unit_id") or f"block:{order_counter}")
            text = str(_get_field(b, "text", "") or "")
            ocr_conf = _get_field(b, "ocr_conf")
            if ocr_conf is None:
                ocr_conf = _get_field(b, "ocr_confidence")
            out.append(_NormalizedUnit(bid, text, p_num, order_counter, ocr_conf, b))
            order_counter += 1

    meta_blocks = _get_field(doc, "metadata_blocks") or []
    for b in meta_blocks:
        bid = str(_get_field(b, "block_id") or _get_field(b, "unit_id") or f"meta:{order_counter}")
        text = str(_get_field(b, "text", "") or "")
        ocr_conf = _get_field(b, "ocr_conf")
        out.append(_NormalizedUnit(bid, text, 0, order_counter, ocr_conf, b))
        order_counter += 1

    return doc_id, out


def _collect_unit_candidates(text: str, cfg: DetectorConfig) -> list[Candidate]:
    """Run L1 (structured), L2 (context), and L3 (NER + family cues) on `text`."""
    if not text:
        return []
    clean_text = _mask_redacted_tags(text)
    cands: list[Candidate] = []
    cands.extend(recognize_emails(clean_text))
    cands.extend(recognize_ids(clean_text, cfg))
    cands.extend(recognize_phones(clean_text, cfg=cfg))
    cands.extend(recognize_govids(clean_text))
    cands.extend(recognize_context(clean_text, cfg))
    person_cands = recognize_persons(clean_text, cfg)
    person_cands = upgrade_family_Context(person_cands, clean_text, cfg)
    cands.extend(person_cands)
    return cands


def detect_and_resolve(
    doc: Any,
    cfg: Any = None,
    *,
    include_allow: bool = True,
) -> list[DetectedFinding]:
    """Run L1-L4 detection, context rules, table typing, per-document name registry, and resolver.

    Deterministic output: sorted by (page_num, block_order, start, end, pii_type).
    Includes ALLOW (Action.KEEP) records with explicit reason codes when include_allow=True.
    """
    resolved_cfg = load_config(cfg)
    doc_id, norm_units = _extract_ordered_units(doc)
    if not norm_units:
        return []

    raw_units = [nu.raw_unit for nu in norm_units]
    col_types = infer_table_column_types(raw_units, resolved_cfg)

    # Pass 1: L1 + L2 + L3 + Table Column Typing per unit
    unit_candidates: dict[str, list[Candidate]] = {}
    for nu in norm_units:
        cands = _collect_unit_candidates(nu.text, resolved_cfg)
        cands = apply_table_column_typing(nu.raw_unit, cands, col_types)
        unit_candidates[nu.unit_id] = cands

    # Pass 2: Seed per-document NameRegistry (L4) and scan for variants / unique surnames
    registry = NameRegistry(resolved_cfg)
    registry.seed_from_document(raw_units, unit_candidates)
    for nu in norm_units:
        clean_text = _mask_redacted_tags(nu.text)
        reg_cands = registry.scan_text(clean_text)
        if reg_cands:
            unit_candidates[nu.unit_id].extend(reg_cands)

    # Pass 3: Resolve overlaps, allow-list precedence, and entity tags in deterministic order
    tag_assigner = EntityTagAssigner()
    ordered_units = sorted(norm_units, key=lambda u: (u.page_num, u.block_order, u.unit_id))
    all_findings: list[tuple[int, int, DetectedFinding]] = []

    for nu in ordered_units:
        resolved = resolve_unit_candidates(
            unit_id=nu.unit_id,
            text=nu.text,
            candidates=unit_candidates.get(nu.unit_id, []),
            cfg=resolved_cfg,
            doc_id=doc_id,
            ocr_confidence=nu.ocr_confidence,
            strict=False,
            include_allow=include_allow,
            tag_assigner=tag_assigner,
        )
        for f in resolved:
            all_findings.append((nu.page_num, nu.block_order, f))

    all_findings.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2].start if item[2].start is not None else -1,
            item[2].end if item[2].end is not None else -1,
            item[2].pii_type.value,
        )
    )
    return [item[2] for item in all_findings]


def detect_strict(
    text: str,
    cfg: Any = None,
    *,
    doc_id: str = "strict",
    block_id: str = "strict",
    include_allow: bool = False,
) -> list[DetectedFinding]:
    """Strict residual-scan detector for the release gate (Task T3.4).

    Precedence & allow-list semantics in strict mode:
    - Uses `cfg.detect.strict_threshold` (default 0.30).
    - Never applies allow-list suppression to HIGH-severity PII types (US_SSN, IN_PAN,
      PL_PESEL, CREDIT_CARD, PASSPORT, DATE_OF_BIRTH, HOME_ADDRESS).
    - Non-HIGH allow-listed items (such as reference IDs like INC-2026-00412) are suppressed
      consistent with the resolver's allow-list precedence rule and not reported as residual leaks
      when `include_allow=False`.
    - Safely ignores placeholder tags such as `[PERSON_003]` or `[EMAIL]`.
    """
    if not text:
        return []
    resolved_cfg = load_config(cfg)
    cands = _collect_unit_candidates(text, resolved_cfg)
    return resolve_unit_candidates(
        unit_id=block_id,
        text=text,
        candidates=cands,
        cfg=resolved_cfg,
        doc_id=doc_id,
        ocr_confidence=None,
        strict=True,
        include_allow=include_allow,
        tag_assigner=EntityTagAssigner(),
    )

