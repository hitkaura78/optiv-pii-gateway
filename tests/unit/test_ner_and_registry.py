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
    by_text = {tricky[c.start:c.end]: (c.start, c.end) for c in cands}
    assert "MARCUS VANCE" in by_text
    assert "PRIYA NAIR" in by_text
    # Verify offsets map back to exact slices in the original ALL-CAPS string
    for expected in ("MARCUS VANCE", "PRIYA NAIR"):
        s, e = by_text[expected]
        assert tricky[s:e] == expected
        assert (s, e) == (tricky.index(expected), tricky.index(expected) + len(expected))


def test_ner_excludes_allowlisted_company_names():
    # "Sterling Vance" looks like a person name if not in allowlist.company_names
    text = "Sterling Vance and Optiv Security met with Elena Rostova yesterday."
    cands_without_allowlist = recognize_persons(text, cfg={"allowlist": {"company_names": []}})
    assert "Sterling Vance" in [text[c.start:c.end] for c in cands_without_allowlist]

    # When "Sterling Vance" is in allowlist.company_names, recognize_persons drops it
    cfg_with_company = {
        "allowlist": {
            "company_names": [
                {"value": "Sterling Vance", "reason": "ALLOW_COMPANY_NAME"},
                {"value": "Optiv Security", "reason": "ALLOW_COMPANY_NAME"},
            ]
        }
    }
    cands = recognize_persons(text, cfg=cfg_with_company)
    matched = [text[c.start:c.end] for c in cands]
    assert matched == ["Elena Rostova"]



def test_variants_handles_single_token_empty_hyphenated_and_middle_initials():
    assert variants("") == []
    assert variants("   ") == []
    assert variants("Madonna") == ["Madonna"]

    # Registering and scanning a single-word name must not crash
    reg = NameRegistry()
    p = reg.register("Madonna")
    assert p is not None and p.surname == ""
    hits = reg.scan_text("Madonna attended the briefing.")
    assert len(hits) == 1 and "Madonna attended the briefing."[hits[0].start:hits[0].end] == "Madonna"

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
            TextUnit(
                "para:0",
                "Follow-up assigned to Elena Rostova, E. Rostova, and E.Rostova; approved by Rostova.",
            ),
        ],
        True,
    )
    f1 = [f for f in detect_and_resolve(doc1) if f.pii_type is PIIType.PERSON and f.action is Action.MASK]
    para_hits = [doc1.units[4].text[f.start:f.end] for f in f1 if f.unit_id == "para:0"]
    assert "Elena Rostova" in para_hits
    assert "E. Rostova" in para_hits
    assert "E.Rostova" in para_hits
    assert "Rostova" in para_hits
    # All linked to the EMP-30912 finding's key and given the same entity_type_tag
    emp1 = [f for f in detect_and_resolve(doc1) if f.unit_id == "table:0/r1/c0/para:0"]
    person_keys = {f.entity_key for f in f1}
    person_tags = {f.entity_type_tag for f in f1}
    assert person_keys == {emp1[0].entity_key}
    assert person_tags == {"[PERSON_001]"}
    # The key is opaque: never the raw ID or name
    assert all("EMP-30912" not in f.model_dump_json() and "Rostova" not in f.entity_key for f in f1)

    # Document 2 has a different person with a similar surname ("Viktor Rostova", EMP-99100)
    # -> must NOT inherit EMP-30912 from Document 1
    doc2 = ExtractedDocument(
        SourceDocument(Path("d2.docx"), SourceKind.DOCX, "2" * 64),
        [
            TextUnit("table:0/r0/c0/para:0", "EMP ID"),
            TextUnit("table:0/r0/c1/para:0", "FULL NAME"),
            TextUnit("table:0/r1/c0/para:0", "EMP-99100"),
            TextUnit("table:0/r1/c1/para:0", "Viktor Rostova"),
            TextUnit("para:0", "Reviewed by V. Rostova and Rostova; E. Rostova is not in this doc."),
        ],
        True,
    )
    f2 = [f for f in detect_and_resolve(doc2) if f.pii_type is PIIType.PERSON and f.action is Action.MASK]
    emp2 = [f for f in detect_and_resolve(doc2) if f.unit_id == "table:0/r1/c0/para:0"]
    text2 = doc2.units[4].text
    viktor = [f for f in f2 if f.unit_id != "para:0" or text2[f.start:f.end] == "V. Rostova"]
    assert {f.entity_key for f in viktor} == {emp2[0].entity_key}
    # "E. Rostova" is a different person in this document: masked, but not merged with Viktor
    other = [f for f in f2 if f.unit_id == "para:0" and text2[f.start:f.end] == "E. Rostova"]
    assert other and other[0].entity_key != emp2[0].entity_key
    assert all("EMP-99100" not in f.model_dump_json() for f in f2)

    # Document 3 has no full-name seed for Rostova -> bare 'Rostova' is NOT linked across documents
    doc3 = ExtractedDocument(
        SourceDocument(Path("d3.docx"), SourceKind.DOCX, "3" * 64),
        [TextUnit("para:0", "Director Rostova reviewed the policy.")],
        True,
    )
    f3 = [f for f in detect_and_resolve(doc3) if f.pii_type is PIIType.PERSON and f.action is Action.MASK]
    assert f3 == []


def test_names_with_initials_are_detected_in_prose_h6():
    # Guide H6 examples: the initial's "." must not cause the whole name to be rejected
    for text, name in (("Statement by Rafael E. Duarte follows.", "Rafael E. Duarte"),
                       ("Approved by S. Marchetti-Lang today", "S. Marchetti-Lang")):
        assert name in [text[c.start:c.end] for c in recognize_persons(text)]
    assert recognize_persons("see A. B. and C.") == []          # initials alone are not a name


def test_ambiguous_initial_variant_links_to_nobody():
    reg = NameRegistry()
    reg.register("Alice Kowalski")
    reg.register("Adam Kowalski")
    assert reg.register("A. Kowalski") is None
    assert len(reg.people) == 2


def test_registry_does_not_merge_two_people_sharing_a_surname_or_short_surname():
    reg = NameRegistry()
    reg.register("Alice Kowalski", entity_key="EMP-10001", has_id_seed=True)
    reg.register("Bob Kowalski", entity_key="EMP-10002", has_id_seed=True)
    reg.register("Noah Chen", entity_key="EMP-10003", has_id_seed=True)  # 4-letter surname (< 5)

    text = "Reviewed by A. Kowalski, B. Kowalski, Manager Kowalski, and Director Chen."
    cands = reg.scan_text(text)
    by_span = {text[c.start:c.end]: c.entity_key for c in cands}
    assert by_span.get("A. Kowalski") == "EMP-10001"
    assert by_span.get("B. Kowalski") == "EMP-10002"
    # Bare shared surname 'Kowalski' and <5-letter surname 'Chen' must NOT be matched alone
    assert "Kowalski" not in by_span
    assert "Chen" not in by_span


def test_strict_mode_never_suppresses_high_severity_and_ignores_tags():
    # Even if an SSN appears alongside an INC- prefix or HQ address is in allowlist,
    # HIGH severity items (US_SSN, HOME_ADDRESS) are never suppressed by allow-list in strict mode.
    hq = "1144 Fifteenth Street, Denver, CO 80202"
    cfg = {"allowlist": {"hq_addresses": [{"value": hq, "reason": "ALLOW_HQ_ADDRESS"}]}}
    text = f"[PERSON_003] at {hq} with SSN 912-34-5678 and ticket INC-2026-00412."
    res = detect_strict(text, cfg=cfg)
    types = {f.pii_type for f in res}
    assert PIIType.US_SSN in types
    assert PIIType.ADDRESS in types
    # Non-HIGH allow-listed INC-2026-00412 is not flagged as a leak
    assert not any(text[f.start:f.end] == "INC-2026-00412" for f in res)


def test_detect_strict_injected_email_ssn_and_clean_text():
    # 1. Injected email found
    t_email = "Redacted summary for [PERSON_001]: residual contact leaked.user@example.com remained."
    res_email = detect_strict(t_email)
    assert len(res_email) == 1
    assert res_email[0].pii_type is PIIType.EMAIL
    assert t_email[res_email[0].start:res_email[0].end] == "leaked.user@example.com"

    # 2. Injected SSN found
    t_ssn = "Record [EMPLOYEE_ID_001] still contains 912-34-5678 in notes."
    res_ssn = detect_strict(t_ssn)
    assert len(res_ssn) == 1
    assert res_ssn[0].pii_type is PIIType.US_SSN
    assert t_ssn[res_ssn[0].start:res_ssn[0].end] == "912-34-5678"

    # 3. Clean redacted text returns nothing ([])
    t_clean = "All PII for [PERSON_001] ([EMAIL_001], [US_SSN_001]) was removed under ticket INC-2026-00412."
    assert detect_strict(t_clean) == []
    assert detect_strict("") == []


