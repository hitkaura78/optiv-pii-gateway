"""Layer 4 per-document Name Registry (Task T3.3 / U-13).

Responsibilities:
- Instantiated per document (never shares state across documents).
- Seeds known people from:
  (a) table rows where the same row contains an EMPLOYEE_ID / DIRECTOR_ID / VENDOR_WORKER_ID,
      linking the person's entity_key to that ID;
  (b) high-confidence PERSON candidates in the document.
- Generates deterministic name variants (full name, First Last, F. Last, F.Last,
  middle-initial forms, hyphenated surnames) while safely handling single-token names,
  empty strings, and repeated whitespace.
- Matches standalone surnames ONLY when:
  (1) the surname is unique across all seeded people in the document,
  (2) len(surname) >= min_unique_surname_length (default 5), and
  (3) corroborated by context or an ID-seeded record (avoiding common surname over-linking).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from detect.config import load_config
from detect.context import parse_table_locator
from detect.models import Candidate
from pii_pipeline.models import PIIType

_COMMON_AMBIGUOUS_SURNAMES = {
    "smith", "jones", "brown", "white", "green", "black", "young", "king",
    "wright", "hill", "scott", "adams", "baker", "hall", "allen", "stone",
    "grant", "house", "field", "fields", "parks", "woods", "marsh", "cross",
}

_SURNAME_CORROBORATION_RE = re.compile(
    r"\b(?:mr\.?|ms\.?|mrs\.?|dr\.?|prof\.?|director|manager|analyst|engineer|"
    r"officer|employee|lead|reported\s+by|assigned\s+to|approved\s+by|signed\s+by|"
    r"contacted|per|according\s+to|handled\s+by|owner|reviewer)\b",
    re.I,
)


def normalize_name(name: str) -> str:
    """Collapse repeated whitespace and strip surrounding punctuation."""
    if not name:
        return ""
    cleaned = " ".join(name.strip().strip(".,;:()[]{}").split())
    return cleaned


def variants(full_name: str) -> list[str]:
    """Generate deterministic surface variants for `full_name` (excluding bare surname).

    Handles empty strings, single-token names, middle names/initials, and hyphenated surnames.
    """
    norm = normalize_name(full_name)
    if not norm:
        return []
    tokens = norm.split()
    if len(tokens) == 1:
        return [tokens[0]]

    first = tokens[0].rstrip(".")
    last = tokens[-1]
    middle_tokens = tokens[1:-1]

    forms: list[str] = [norm, f"{first} {last}"]
    if first:
        fi = first[0]
        forms.append(f"{fi}. {last}")
        forms.append(f"{fi}.{last}")
        forms.append(f"{fi} {last}")

    if middle_tokens:
        mid = middle_tokens[0].rstrip(".")
        if mid:
            mi = mid[0]
            forms.append(f"{first} {mi}. {last}")
            forms.append(f"{first} {mi} {last}")
            if first:
                forms.append(f"{first[0]}. {mi}. {last}")

    # Preserve order and deduplicate case-insensitively
    seen: set[str] = set()
    unique_forms: list[str] = []
    for f in forms:
        f_clean = " ".join(f.split())
        if f_clean and f_clean.lower() not in seen:
            seen.add(f_clean.lower())
            unique_forms.append(f_clean)
    return unique_forms


@dataclass
class RegisteredPerson:
    canonical_name: str
    entity_key: str
    first_name: str
    surname: str
    has_id_seed: bool = False
    surface_variants: list[str] = field(default_factory=list)


class NameRegistry:
    """Per-document registry of person names and their deterministic variants."""

    def __init__(self, cfg: Any = None) -> None:
        self.cfg = load_config(cfg)
        self.people: list[RegisteredPerson] = []
        self._by_norm_name: dict[str, RegisteredPerson] = {}
        self._surname_counts: dict[str, int] = {}

    def register(self, full_name: str, entity_key: str | None = None, has_id_seed: bool = False) -> RegisteredPerson | None:
        norm = normalize_name(full_name)
        if not norm:
            return None
        tokens = norm.split()
        first = tokens[0].rstrip(".")
        surname = tokens[-1] if len(tokens) >= 2 else ""
        norm_key = norm.lower()

        # Check if already registered under full name or First + Last
        first_last_key = f"{first.lower()} {surname.lower()}".strip()
        existing = self._by_norm_name.get(norm_key) or (
            self._by_norm_name.get(first_last_key) if surname else None
        )
        if existing is not None:
            if entity_key and (not existing.has_id_seed or existing.entity_key == existing.canonical_name.lower()):
                existing.entity_key = entity_key
            if has_id_seed:
                existing.has_id_seed = True
            for v in variants(norm):
                if v not in existing.surface_variants:
                    existing.surface_variants.append(v)
            return existing

        resolved_key = entity_key if entity_key else first_last_key
        person = RegisteredPerson(
            canonical_name=norm,
            entity_key=resolved_key,
            first_name=first,
            surname=surname,
            has_id_seed=has_id_seed,
            surface_variants=variants(norm),
        )
        self.people.append(person)
        self._by_norm_name[norm_key] = person
        if surname:
            self._by_norm_name.setdefault(first_last_key, person)
            s_low = surname.lower()
            self._surname_counts[s_low] = self._surname_counts.get(s_low, 0) + 1
        return person

    def seed_from_document(
        self,
        units: list[Any],
        unit_candidates: dict[str, list[Candidate]],
    ) -> None:
        """Seed registry from table rows with IDs + high-confidence PERSON candidates."""
        # Group table cells by (table_id, row) to link row IDs with row PERSON names
        row_ids: dict[tuple[str, int], str] = {}
        row_names: dict[tuple[str, int], list[str]] = {}

        for u in units:
            if isinstance(u, dict):
                uid = str(u.get("unit_id") or u.get("block_id") or "")
                text = str(u.get("text", "") or "")
            else:
                uid = str(getattr(u, "unit_id", None) or getattr(u, "block_id", "") or "")
                text = str(getattr(u, "text", "") or "")
            cands = unit_candidates.get(uid, [])
            loc = parse_table_locator(u)

            for c in cands:
                if loc is not None and c.entity_type in ("EMPLOYEE_ID", "DIRECTOR_ID", "VENDOR_WORKER_ID"):
                    tid, row, _ = loc
                    row_ids.setdefault((tid, row), text[c.start:c.end].strip())
                elif loc is not None and c.pii_type is PIIType.PERSON and c.score >= 0.75:
                    tid, row, _ = loc
                    row_names.setdefault((tid, row), []).append(text[c.start:c.end])

        # 1. Seed table-row linked people first (with their employee/director/vendor ID as entity_key)
        for row_key in sorted(row_names.keys()):
            emp_id = row_ids.get(row_key)
            for raw_name in row_names[row_key]:
                self.register(raw_name, entity_key=emp_id, has_id_seed=bool(emp_id))

        # 2. Seed remaining high-confidence PERSON candidates in document order
        for u in units:
            if isinstance(u, dict):
                uid = str(u.get("unit_id") or u.get("block_id") or "")
                text = str(u.get("text", "") or "")
            else:
                uid = str(getattr(u, "unit_id", None) or getattr(u, "block_id", "") or "")
                text = str(getattr(u, "text", "") or "")
            for c in unit_candidates.get(uid, []):
                if c.pii_type is PIIType.PERSON and c.score >= 0.78:
                    span_text = normalize_name(text[c.start:c.end])
                    if len(span_text.split()) >= 2:
                        self.register(span_text, entity_key=c.entity_key or None, has_id_seed=False)

    def scan_text(self, text: str) -> list[Candidate]:
        """Find registered name variants and corroborated unique surnames in `text`."""
        if not text or not self.people:
            return []
        min_surname_len = self.cfg.detect.min_unique_surname_length
        out: list[Candidate] = []

        for person in self.people:
            # 1. Multi-token variants (full name, First Last, F. Last, F.Last, middle-initial forms)
            for var in person.surface_variants:
                # Allow flexible whitespace between tokens in the regex
                tokens = [re.escape(t) for t in var.split()]
                pat_str = r"\s+".join(tokens)
                pat = re.compile(rf"(?<![A-Za-z0-9_-])({pat_str})(?![A-Za-z0-9_-])", re.I)
                for m in pat.finditer(text):
                    s, e = m.span(1)
                    # Require capitalized initials in the matched surface
                    matched = text[s:e]
                    if not matched[0].isupper():
                        continue
                    out.append(
                        Candidate(
                            start=s,
                            end=e,
                            pii_type=PIIType.PERSON,
                            entity_type="PERSON",
                            score=0.88 if person.has_id_seed else 0.84,
                            validator="registry:name_variant",
                            detector="registry:person_variant",
                            category="PERSON",
                            severity="MEDIUM",
                            entity_key=person.entity_key,
                            validated=person.has_id_seed,
                        )
                    )

            # 2. Unique surname alone (only if unique in document and >= min_surname_len, default 5)
            surname = person.surname
            if (
                surname
                and len(surname) >= min_surname_len
                and self._surname_counts.get(surname.lower(), 0) == 1
            ):
                s_low = surname.lower()
                is_common = s_low in _COMMON_AMBIGUOUS_SURNAMES
                spat = re.compile(rf"(?<![A-Za-z0-9_-])({re.escape(surname)})(?![A-Za-z0-9_-])")
                for m in spat.finditer(text):
                    s, e = m.span(1)
                    ctx_before = text[max(0, s - 40):s]
                    ctx_after = text[e:min(len(text), e + 25)]
                    has_corroboration = bool(
                        _SURNAME_CORROBORATION_RE.search(ctx_before)
                        or _SURNAME_CORROBORATION_RE.search(ctx_after)
                    )
                    if is_common and not has_corroboration:
                        continue
                    out.append(
                        Candidate(
                            start=s,
                            end=e,
                            pii_type=PIIType.PERSON,
                            entity_type="PERSON",
                            score=0.76 if (has_corroboration or person.has_id_seed) else 0.72,
                            validator="registry:unique_surname",
                            detector="registry:unique_surname",
                            category="PERSON",
                            severity="MEDIUM",
                            entity_key=person.entity_key,
                            validated=False,
                        )
                    )

        out.sort(key=lambda c: (c.start, -(c.end - c.start), c.entity_key))
        return out

