"""Unit tests for Layer 3 NER, Layer 4 NameRegistry, and Strict Mode (Tasks T3.1, T3.3, T3.4 / Spec U-13)."""
from pathlib import Path

from detect.engine import detect_and_resolve, detect_strict
from detect.ner import recognize_persons, titlecase_preserve_offsets
from detect.registry import NameRegistry, variants
from pii_pipeline.models import (
    Action,
    ExtractedDocument,
    PIIType,
    SourceDocument,
    SourceKind,
    TextUnit,
)


def test_titlecase_preserve_offsets_keeps_exact_unicode_length():
    # '\u0130' (Latin Capital Letter I with dot above) expands to 2 chars under str.lower()/str.title()
    tricky = "İSTANBUL REPORT BY MARCUS VANCE AND PRIYA NAIR"
    titled = titlecase_preserve_offsets(tricky)
    assert len(titled) == len(tricky)

    cands = recognize_persons(tricky)
    matched = [tricky[c.start:c.end] for c in cands]
    assert "MARCUS VANCE" in matched
    assert "PRIYA NAIR" in matched


def test_ner_excludes_allowlisted_company_names():
    text = "Optiv Security and Global Trust Bank met with Elena Rostova yesterday."
    cands = recognize_persons(text)
    matched = [text[c.start:c.end] for c in cands]
    assert "Elena Rostova" in matched
    assert not any("Optiv" in m or "Global Trust" in m for m in matched)


def test_variants_handles_single_token_empty_hyphenated_and_middle_initials():
    assert variants("") == []
    assert variants("   ") == []
    assert variants("Madonna") == ["Madonna"]

    v_hyph = variants("Sarah   Al-Mansoor")
    assert "Sarah Al-Mansoor" in v_hyph
    assert "S. Al-Mansoor" in v_hyph
    assert "S.Al-Mansoor" in v_hyph

    v_mid = variants("Priya K. Nair")
    assert "Priya K. Nair" in v_mid
    assert "Priya Nair" in v_mid
    assert "P. Nair" in v_mid
    assert "P.Nair" in v_mid


def test_registry_links_variants_within_one_doc_and_never_across_docs():
    doc1 = ExtractedDocument(
        SourceDocument(Path("d1.docx"), SourceKind.DOCX, "1" * 64),
        [
            TextUnit("table:0/r0/c0/para:0", "EMP ID"),
            TextUnit("table:0/r0/c1/para:0", "FULL NAME"),
            TextUnit("table:0/r1/c0/para:0", "EMP-30912"),
            TextUnit("table:0/r1/c1/para:0", "Elena K. Rostova"),
            TextUnit("para:0", "Follow-up assigned to E. Rostova and E.Rostova; approved by Director Rostova."),
        ],
        True,
    )
    f1 = [f for f in detect_and_resolve(doc1) if f.pii_type is PIIType.PERSON and f.action is Action.MASK]
    para_hits = [doc1.units[4].text[f.start:f.end] for f in f1 if f.unit_id == "para:0"]
    assert "E. Rostova" in para_hits
    assert "E.Rostova" in para_hits
    assert "Rostova" in para_hits
    # All linked to EMP-30912 and given the same entity_type_tag
    person_keys = {f.entity_key for f in f1}
    person_tags = {f.entity_type_tag for f in f1}
    assert person_keys == {"EMP-30912"}
    assert person_tags == {"[PERSON_001]"}

    # Document 2 has no full-name seed for Rostova -> bare 'Rostova' is NOT linked across documents
    doc2 = ExtractedDocument(
        SourceDocument(Path("d2.docx"), SourceKind.DOCX, "2" * 64),
        [TextUnit("para:0", "Director Rostova reviewed the policy.")],
        True,
    )
    f2 = [f for f in detect_and_resolve(doc2) if f.pii_type is PIIType.PERSON and f.action is Action.MASK]
    assert f2 == []


def test_registry_does_not_merge_two_people_sharing_a_surname():
    reg = NameRegistry()
    reg.register("Alice Kowalski", entity_key="EMP-10001", has_id_seed=True)
    reg.register("Bob Kowalski", entity_key="EMP-10002", has_id_seed=True)

    text = "Reviewed by A. Kowalski, B. Kowalski, and Manager Kowalski."
    cands = reg.scan_text(text)
    by_span = {text[c.start:c.end]: c.entity_key for c in cands}
    assert by_span.get("A. Kowalski") == "EMP-10001"
    assert by_span.get("B. Kowalski") == "EMP-10002"
    # Bare shared surname 'Kowalski' must NOT be linked to either person
    assert "Kowalski" not in by_span


def test_strict_mode_never_suppresses_high_severity_and_ignores_tags():
    # Even if an SSN appears alongside an INC- prefix orHQ address is in strict mode,
    # HIGH severity items (US_SSN, HOME_ADDRESS) are never suppressed by allow-list in strict mode.
    hq = "1144 Fifteenth Street, Denver, CO 80202"
    text = f"[PERSON_003] at {hq} with SSN 912-34-5678 and ticket INC-2026-00412."
    res = detect_strict(text)
    types = {f.pii_type for f in res}
    assert PIIType.US_SSN in types
    assert PIIType.ADDRESS in types
    # Non-HIGH allow-listed INC-2026-00412 is not flagged as a leak
    assert not any(text[f.start:f.end] == "INC-2026-00412" for f in res)

