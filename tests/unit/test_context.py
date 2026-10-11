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
    # Positives within 25 characters of cue
    p1 = "Employee DOB 14 July 1991 verified."
    res1 = recognize_dob(p1)
    assert len(res1) == 1 and p1[res1[0].start:res1[0].end] == "14 July 1991"

    p2 = "Customer date of birth, 1991-07-14 on file."
    res2 = recognize_dob(p2)
    assert len(res2) == 1 and p2[res2[0].start:res2[0].end] == "1991-07-14"

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

    # Uncued dates -> NOT matched
    assert recognize_dob("Policy effective 1 July 2025 across regions.") == []
    assert recognize_dob("Thank you for calling us on 15 April 2024.") == []


def test_last_four_fragments_within_30_chars():
    text = "Paid with credit card ending 2218 and account (**9041); SSN (ending in 6789)."
    res = recognize_fragments(text)
    found = [(c.entity_type, text[c.start:c.end]) for c in res]
    assert found == [
        ("CARD_LAST4", "2218"),
        ("ACCOUNT_LAST4", "9041"),
        ("SSN_LAST4", "6789"),
    ]
    # Uncued 4-digit number or >30 chars away is not matched
    far = "Card" + (" x" * 20) + " ending 2218"
    assert recognize_fragments(far) == []


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

