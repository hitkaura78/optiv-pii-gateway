"""Unit tests for L2 context rules and table column typing (Tasks T2.1, T3.2 / Spec U-11, U-12)."""
from pathlib import Path

from detect.context import recognize_context
from detect.engine import detect_and_resolve
from detect.recognizers.address import recognize_addresses
from detect.recognizers.dob import recognize_dob
from detect.recognizers.fragments import recognize_fragments
from pii_pipeline.models import (
    Action,
    ExtractedDocument,
    PIIType,
    SourceDocument,
    SourceKind,
    TextUnit,
)


def test_dob_context_window_positives_and_negatives():
    # Positives within 25 characters of "DOB", "date of birth", or "born" across supported date formats
    supported_samples = [
        ("Employee DOB 14 July 1991 verified.", "14 July 1991"),
        ("DOB: 14-Jul-1991", "14-Jul-1991"),
        ("Born July 14, 1991 in Chicago.", "July 14, 1991"),
        ("Customer date of birth, 1991-07-14 on file.", "1991-07-14"),
        ("DOB 14/07/1991", "14/07/1991"),
        ("date of birth: 07/14/1991", "07/14/1991"),
        ("born on 14.07.1991", "14.07.1991"),
    ]
    for sample, expected in supported_samples:
        res = recognize_dob(sample)
        assert len(res) == 1, f"Expected 1 DOB in {sample!r}, got {res}"
        assert sample[res[0].start:res[0].end] == expected

    # Boundary: exactly 25 characters between cue end and date start
    gap_25 = ":" + (" " * 24)
    assert len(gap_25) == 25
    p_exact = f"DOB{gap_25}14 July 1991"
    assert len(recognize_dob(p_exact)) == 1

    # Beyond 25 characters -> NOT matched
    gap_26 = ":" + (" " * 25)
    assert len(gap_26) == 26
    p_beyond = f"DOB{gap_26}14 July 1991"
    assert recognize_dob(p_beyond) == []

    # Required negatives: "effective 1 July" and "calling us on 15 April" (with and without year)
    for neg_sample in (
        "effective 1 July",
        "calling us on 15 April",
        "Policy effective 1 July 2025 across regions.",
        "Thank you for calling us on 15 April 2024.",
    ):
        assert recognize_dob(neg_sample) == []
        assert recognize_context(neg_sample) == []


def test_last_four_fragments_within_30_chars():
    # Exact required positive: "card ending 2218"
    c1 = recognize_context("card ending 2218")
    assert len(c1) == 1 and c1[0].entity_type == "CARD_LAST4"
    assert "card ending 2218"[c1[0].start:c1[0].end] == "2218"

    text = "Paid with credit card ending 2218 and account (**9041); SSN (ending in 6789)."
    res = recognize_fragments(text)
    found = [(c.entity_type, text[c.start:c.end]) for c in res]
    assert found == [
        ("CARD_LAST4", "2218"),
        ("ACCOUNT_LAST4", "9041"),
        ("SSN_LAST4", "6789"),
    ]
    # Uncued 4-digit number, >30 chars away, or across sentence boundary is not matched
    far = "Card" + (" x" * 20) + " ending 2218"
    assert recognize_fragments(far) == []
    assert recognize_fragments("Missing card. Page ending 2218") == []


def test_home_address_vs_hq_address_allowlist():
    home = "742 Evergreen Terrace, Springfield, IL 62704"
    hq = "1144 Fifteenth Street, Denver, CO 80202"
    text = f"Ship to {home}; corporate office at {hq}."

    # Recognizer matches both street addresses
    addr_cands = recognize_addresses(text)
    assert [text[c.start:c.end] for c in addr_cands] == [home, hq]

    # Resolver masks the home address and suppresses the HQ address with ALLOW_HQ_ADDRESS
    doc = ExtractedDocument(
        SourceDocument(Path("a.docx"), SourceKind.DOCX, "b" * 64),
        [TextUnit("para:0", text)],
        True,
    )
    findings = detect_and_resolve(doc)
    addr_findings = [f for f in findings if f.pii_type is PIIType.ADDRESS]
    assert len(addr_findings) == 2
    masked = [f for f in addr_findings if f.action is Action.MASK]
    allowed = [f for f in addr_findings if f.action is Action.KEEP]
    assert len(masked) == 1 and text[masked[0].start:masked[0].end] == home
    assert len(allowed) == 1 and text[allowed[0].start:allowed[0].end] == hq
    assert allowed[0].reason == "ALLOW_HQ_ADDRESS"


def test_table_column_typing_and_raci_header_scanning():
    # 3-column table + RACI header cell containing a person name
    units = [
        TextUnit("table:0/r0/c0/para:0", "EMP ID"),
        TextUnit("table:0/r0/c1/para:0", "Responsible: Priya Nair"),
        TextUnit("table:0/r0/c2/para:0", "MOBILE"),
        TextUnit("table:0/r1/c0/para:0", "EMP-20415"),
        TextUnit("table:0/r1/c1/para:0", "Noah Novak"),
        TextUnit("table:0/r1/c2/para:0", "+1 (555) 0114"),
    ]
    doc = ExtractedDocument(
        SourceDocument(Path("dir.docx"), SourceKind.DOCX, "c" * 64),
        units,
        True,
    )
    findings = [f for f in detect_and_resolve(doc) if f.action is Action.MASK]
    by_unit = {f.unit_id: f for f in findings}

    # Header cell itself was scanned and "Priya Nair" detected
    assert "table:0/r0/c1/para:0" in by_unit
    assert by_unit["table:0/r0/c1/para:0"].pii_type is PIIType.PERSON

    # Row 1 cells typed and boosted by column headers; Noah Novak linked to EMP-20415
    assert "table:0/r1/c0/para:0" in by_unit
    assert "table:0/r1/c1/para:0" in by_unit
    assert "table:0/r1/c2/para:0" in by_unit
    assert "table_col:person" in by_unit["table:0/r1/c1/para:0"].validator
    assert by_unit["table:0/r1/c1/para:0"].entity_key == "EMP-20415"


def test_r05_dob_no_sentence_boundary_no_negative_cue_and_matching_separators():
    # Sentence boundary + negative cue must not match
    assert recognize_dob("Missing DOB. Effective 01/05/2025") == []
    assert recognize_dob("DOB; 01/05/2025") == []
    assert recognize_dob("DOB\n01/05/2025") == []
    for neg in ("effective", "updated", "due", "issued", "expires"):
        assert recognize_dob(f"DOB {neg} 01/05/2025") == []

    # Mixed separators must not match
    assert recognize_dob("DOB 14/07-1991") == []
    assert recognize_dob("DOB 1991-07/14") == []

    # Valid DOB formats still work
    res = recognize_dob("DOB 14 July 1991")
    assert len(res) == 1 and "DOB 14 July 1991"[res[0].start:res[0].end] == "14 July 1991"
    res_dots = recognize_dob("D.O.B. 14/07/1991")
    assert len(res_dots) == 1 and "D.O.B. 14/07/1991"[res_dots[0].start:res_dots[0].end] == "14/07/1991"

