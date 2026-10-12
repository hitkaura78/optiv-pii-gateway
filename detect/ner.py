"""Layer 3 PERSON recognition with length-preserving ALL-CAPS title-casing (Task T3.1).

Loads spaCy (en_core_web_md / en_core_web_sm) once if available, combined with a
deterministic offline person-name recognizer so the module operates offline without
network downloads. Maps spans from title-cased ALL-CAPS blocks back to original offsets
using a strict 1:1 character-length-preserving transform (safe on Unicode chars like 'İ').
Drops spans matching allow-listed company names from config/allowlist.yaml.
"""
from __future__ import annotations

import re
from typing import Any

from detect.config import load_config
from detect.models import Candidate
from pii_pipeline.models import PIIType

_SPACY_NLP: Any = None
_SPACY_LOADED: bool = False

# Non-person capitalized tokens that must never be mistaken for a person's first/last name
_NON_NAME_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "street", "st", "avenue", "ave", "road", "rd", "boulevard", "blvd", "lane", "ln",
    "drive", "dr", "court", "ct", "way", "terrace", "ter", "place", "pl", "suite",
    "north", "south", "east", "west", "floor", "building", "city", "state",
    "incident", "response", "policy", "document", "summary", "executive", "report",
    "customer", "letter", "directory", "employee", "employees", "national", "security",
    "social", "number", "account", "card", "credit", "debit", "mobile", "phone",
    "email", "address", "home", "residential", "office", "headquarters", "department",
    "division", "section", "table", "figure", "page", "slide", "notes", "speaker",
    "responsible", "accountable", "consulted", "informed", "raci", "matrix", "status",
    "review", "approved", "draft", "version", "confidential", "internal", "external",
    "vendor", "merchant", "director", "manager", "engineer", "analyst", "officer",
    "chief", "president", "vice", "senior", "junior", "lead", "team", "group",
    "bank", "trust", "global", "corp", "corporation", "inc", "llc", "ltd", "plc",
    "company", "services", "solutions", "systems", "technologies", "optiv", "acme",
    "united", "states", "kingdom", "poland", "india", "france", "singapore", "sweden",
    "birth", "date", "born", "effective", "total", "amount", "order", "ticket", "form",
    "passport", "pesel", "pan", "ssn", "iban", "aadhaar",
    "and", "or", "by", "for", "with", "from", "to", "in", "on", "at", "of", "the",
    "as", "is", "are", "was", "were", "be", "this", "that", "these", "those",
    "emp", "id", "dir", "mer", "staff", "worker", "full", "name", "first", "last",
    "contact", "details", "mail", "today", "header", "host", "follow", "up",
    "assigned", "reviewed", "approved", "signed", "shipped", "logged", "paid",
}

# Title-cased 2- or 3-token person name pattern (First [M./Middle] Last[-Last])
_PERSON_SPAN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"([A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{1,20}"
    r"(?:\s+(?:[A-Z]\.|[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{1,20}))?"
    r"\s+[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{1,22}(?:-[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{1,22})?)"
    r"(?![A-Za-z0-9_-])"
)


def _get_spacy_nlp() -> Any:
    global _SPACY_NLP, _SPACY_LOADED
    if _SPACY_LOADED:
        return _SPACY_NLP
    _SPACY_LOADED = True
    try:
        import spacy  # type: ignore
        for model_name in ("en_core_web_md", "en_core_web_sm"):
            try:
                _SPACY_NLP = spacy.load(model_name, disable=["parser", "lemmatizer"])
                return _SPACY_NLP
            except Exception:
                continue
    except ImportError:
        pass
    return None


def titlecase_preserve_offsets(text: str) -> str:
    """Convert an ALL-CAPS or mixed string to Title Case while guaranteeing len(out) == len(text).

    Python's `str.title()` / `str.lower()` can expand certain Unicode characters
    (e.g., '\\u0130' -> 'i\\u0307', length 1 -> 2), which would shift all subsequent character
    offsets. This function transforms character-by-character and preserves exact 1:1 index mapping.
    """
    out: list[str] = []
    in_word = False
    for ch in text:
        if ch.isalpha():
            if not in_word:
                tc = ch.upper()
                out.append(tc if len(tc) == 1 else ch)
                in_word = True
            else:
                lc = ch.lower()
                out.append(lc if len(lc) == 1 else ch)
        else:
            out.append(ch)
            if ch not in ("'", "’"):
                in_word = False
    return "".join(out)


def is_mostly_uppercase(text: str) -> bool:
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) < 4:
        return False
    upper = sum(1 for c in alpha if c.isupper())
    return (upper / len(alpha)) >= 0.70


def _is_valid_person_span(span_text: str, company_names_lower: set[str]) -> bool:
    cleaned = " ".join(span_text.split())
    if not cleaned or len(cleaned) < 4:
        return False
    low = cleaned.lower()
    if low in company_names_lower or any(comp in low for comp in company_names_lower):
        return False
    # Keep a trailing "." so initials ("E.", "S.") are still recognisable as initials below.
    tokens = [t.strip(",;:()").lstrip(".") for t in cleaned.split()]
    tokens = [t for t in tokens if t.rstrip(".")]
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    if all(len(t.rstrip(".")) == 1 for t in tokens):    # "A. B." is not a name
        return False
    for tok in tokens:
        tok_clean = tok.rstrip(".")
        if len(tok_clean) == 1 and tok.endswith("."):
            continue
        if len(tok_clean) < 2:
            return False
        for part in tok_clean.split("-"):
            if part.lower() in _NON_NAME_WORDS:
                return False
            if not part[0].isupper():
                return False
    return True


_CAP_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:[A-Z]\.|[A-ZÀ-ÖØ-Þ\u0130][a-zß-öø-ÿ]{1,22}(?:-[A-ZÀ-ÖØ-Þ\u0130][a-zß-öø-ÿ]{1,22})?)"
    r"(?![A-Za-z0-9_-])"
)


def _extract_from_surface(
    surface: str,
    original_text: str,
    company_names_lower: set[str],
    score_base: float,
) -> list[Candidate]:
    found: list[Candidate] = []
    nlp = _get_spacy_nlp()
    if nlp is not None:
        doc = nlp(surface)
        for ent in getattr(doc, "ents", ()):
            if ent.label_ != "PERSON":
                continue
            s, e = ent.start_char, ent.end_char
            while s < e and original_text[s] in " \t\r\n.,;:()[]":
                s += 1
            while e > s and original_text[e - 1] in " \t\r\n.,;:()[]":
                e -= 1
            if s >= e:
                continue
            probe = surface[s:e]
            if not _is_valid_person_span(probe, company_names_lower):
                continue
            found.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.PERSON,
                    entity_type="PERSON",
                    score=score_base,
                    validator="ner:spacy_person",
                    detector="spacy_person",
                    category="PERSON",
                    severity="MEDIUM",
                    entity_key=" ".join(probe.split()),
                    validated=False,
                )
            )

    # Group consecutive capitalized name tokens separated only by single/multiple spaces,
    # breaking runs whenever a token is in _NON_NAME_WORDS or punctuation appears.
    tokens = list(_CAP_TOKEN_RE.finditer(surface))
    run: list[re.Match[str]] = []

    def flush_run(current_run: list[re.Match[str]]) -> None:
        if not (2 <= len(current_run) <= 3):
            return
        s, e = current_run[0].start(), current_run[-1].end()
        probe = surface[s:e]
        if not _is_valid_person_span(probe, company_names_lower):
            return
        found.append(
            Candidate(
                start=s,
                end=e,
                pii_type=PIIType.PERSON,
                entity_type="PERSON",
                score=score_base,
                validator="ner:person_pattern",
                detector="spacy_person",
                category="PERSON",
                severity="MEDIUM",
                entity_key=" ".join(probe.split()),
                validated=False,
            )
        )

    for m in tokens:
        tok_text = m.group().rstrip(".")
        is_stop = any(part.lower() in _NON_NAME_WORDS for part in tok_text.split("-"))
        if is_stop:
            flush_run(run)
            run = []
            continue
        if run:
            prev_end = run[-1].end()
            between = surface[prev_end:m.start()]
            if not between or any(ch not in " \t" for ch in between):
                flush_run(run)
                run = []
        run.append(m)
    flush_run(run)
    return found


def recognize_persons(text: str, cfg: Any = None) -> list[Candidate]:
    """Run PERSON detection on `text` and on a length-preserving title-cased copy if ALL-CAPS."""
    if not text or not text.strip():
        return []
    resolved_cfg = load_config(cfg)
    company_names_lower: set[str] = set()
    for entry in resolved_cfg.allowlist.company_names:
        val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        if val.strip():
            company_names_lower.add(" ".join(val.strip().lower().split()))

    raw: list[Candidate] = []
    raw.extend(_extract_from_surface(text, text, company_names_lower, score_base=0.82))

    # Also check ALL-CAPS blocks or uppercase segments via length-preserving title-cased copy
    if is_mostly_uppercase(text) or re.search(r"\b[A-Z]{2,20}\s+[A-Z]{2,22}\b", text):
        titled = titlecase_preserve_offsets(text)
        assert len(titled) == len(text)
        for c in _extract_from_surface(titled, text, company_names_lower, score_base=0.78):
            # Only accept uppercase-derived spans if the original text was uppercase or titlecase
            orig_slice = text[c.start:c.end]
            if orig_slice.isupper() or orig_slice[0].isupper():
                raw.append(c)

    # Deduplicate overlapping spans (prefer longest span, then highest score)
    raw.sort(key=lambda c: (-(c.end - c.start), -c.score, c.start))
    kept: list[Candidate] = []
    taken: list[tuple[int, int]] = []
    for cand in raw:
        if any(cand.start < te and ts < cand.end for ts, te in taken):
            continue
        taken.append((cand.start, cand.end))
        kept.append(cand)

    kept.sort(key=lambda c: (c.start, -(c.end - c.start)))
    return kept
