"""Unit tests for candidate resolver, overlap merging, allow-list precedence, and determinism (Task T2.3)."""
import random

from detect.models import Candidate
from detect.resolver import resolve_unit_candidates
from pii_pipeline.models import Action, PIIType


def test_same_span_merges_detectors_and_overlap_prefers_longest_then_priority():
    text = "Contact jane.doe@example.com or 123-45-6789."
    c1 = Candidate(8, 28, PIIType.EMAIL, "EMAIL_ADDRESS", 0.95, "syntax:email", "regex:email", "CONTACT", "MEDIUM", validated=True)
    c2 = Candidate(8, 28, PIIType.EMAIL, "EMAIL_ADDRESS", 0.85, "table_col:email", "context:table_column", "CONTACT", "MEDIUM", validated=True)
    # Shorter overlapping candidate inside email should lose to longer span
    c_short = Candidate(17, 28, PIIType.PERSON, "PERSON", 0.99, "ner", "spacy_person", "PERSON", "MEDIUM")

    res = resolve_unit_candidates("para:0", text, [c_short, c2, c1], include_allow=False)
    assert len(res) == 1
    assert res[0].pii_type is PIIType.EMAIL
    assert set(res[0].detectors) == {"regex:email", "context:table_column"}


def test_equal_span_tie_breaks_by_priority_not_raw_score():
    text = "ID 123-45-6789"
    # Even if a PHONE or PERSON candidate has a higher raw float score on the exact same span,
    # GOV_ID priority wins.
    gov = Candidate(3, 14, PIIType.US_SSN, "US_SSN", 0.80, "ssn_rules:pass", "regex:us_ssn", "GOVERNMENT_ID", "HIGH", validated=True)
    ph = Candidate(3, 14, PIIType.PHONE, "PHONE_NUMBER", 0.99, "phone", "regex:phone", "CONTACT", "MEDIUM", validated=True)
    res = resolve_unit_candidates("para:0", text, [ph, gov], include_allow=False)
    assert len(res) == 1
    assert res[0].pii_type is PIIType.US_SSN


def test_allowlist_suppression_with_reason_codes():
    text = "Mail support@example.com or call +1-800-555-0100 regarding INC-2026-00412 ($1,204.00)."
    c_mail = Candidate(5, 24, PIIType.EMAIL, "EMAIL_ADDRESS", 0.95, "syntax:email", "regex:email", "CONTACT", "MEDIUM", validated=True)
    c_phone = Candidate(33, 48, PIIType.PHONE, "PHONE_NUMBER", 0.90, "phone", "regex:phone", "CONTACT", "MEDIUM", validated=True)

    res = resolve_unit_candidates("para:0", text, [c_mail, c_phone], include_allow=True)
    reasons = {f.reason: f for f in res}
    assert "ALLOW_GENERIC_MAILBOX" in reasons and reasons["ALLOW_GENERIC_MAILBOX"].action is Action.KEEP
    assert "ALLOW_HOTLINE_NUMBER" in reasons and reasons["ALLOW_HOTLINE_NUMBER"].action is Action.KEEP
    assert "ALLOW_REF_ID_INC" in reasons and reasons["ALLOW_REF_ID_INC"].action is Action.KEEP
    assert "ALLOW_MONEY_AMOUNT" in reasons and reasons["ALLOW_MONEY_AMOUNT"].action is Action.KEEP


def test_allowlist_conflict_with_validated_high_pii_emits_review():
    # If a validated HIGH-severity SSN conflicts with an allow-list rule (e.g. embedded in an INC- token
    # or custom allow-list entry), the resolver must NOT silently allow it -> Action.REVIEW + ALLOWLIST_CONFLICT
    text = "Ref INC-123-45-6789 logged."
    ssn_cand = Candidate(
        start=8,
        end=19,
        pii_type=PIIType.US_SSN,
        entity_type="US_SSN",
        score=0.95,
        validator="ssn_rules:pass",
        detector="regex:us_ssn",
        category="GOVERNMENT_ID",
        severity="HIGH",
        validated=True,
    )
    res = resolve_unit_candidates("para:0", text, [ssn_cand], include_allow=True)
    reviews = [f for f in res if f.action is Action.REVIEW]
    assert len(reviews) == 1
    assert reviews[0].reason == "ALLOWLIST_CONFLICT"
    assert text[reviews[0].start:reviews[0].end] == "123-45-6789"


def test_shuffled_candidates_produce_identical_output_and_low_ocr_flag():
    text = "Email a@example.com, phone 415-555-0132, SSN 912-34-5678."
    cands = [
        Candidate(6, 19, PIIType.EMAIL, "EMAIL_ADDRESS", 0.95, "syntax:email", "regex:email", "CONTACT", "MEDIUM", validated=True),
        Candidate(27, 39, PIIType.PHONE, "PHONE_NUMBER", 0.85, "nanp", "regex:phone", "CONTACT", "MEDIUM", validated=True),
        Candidate(45, 56, PIIType.US_SSN, "US_SSN", 0.95, "ssn", "regex:us_ssn", "GOVERNMENT_ID", "HIGH", validated=True),
    ]
    baseline = [
        f.model_dump_json()
        for f in resolve_unit_candidates("page:1/ocr", text, cands, ocr_confidence=0.62)
    ]
    rng = random.Random(42)
    for _ in range(10):
        shuffled = list(cands)
        rng.shuffle(shuffled)
        run = [
            f.model_dump_json()
            for f in resolve_unit_candidates("page:1/ocr", text, shuffled, ocr_confidence=0.62)
        ]
        assert run == baseline

    findings = resolve_unit_candidates("page:1/ocr", text, cands, ocr_confidence=0.62, include_allow=False)
    assert all(f.action is Action.MASK and "LOW_OCR_CONFIDENCE" in f.reason for f in findings)

