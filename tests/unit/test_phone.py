"""Unit tests for phone recognizer across regions + lenient fallback (Task T1.3 / Spec U-07)."""
from detect.recognizers.phone import phones
from pii_pipeline.models import PIIType


def test_seven_distinct_phone_formats_including_lenient_h7():
    samples = [
        "+1 (555) 0114",          # H7: 8 digits total (+1 + 555 + 0114) via lenient fallback
        "415-555-0132",           # NANP dashed
        "(212) 555-0199",         # NANP parens
        "+1 650 555 0144",        # US international spaced
        "+44 20 7946 0958",       # UK international
        "+91 98765 43210",        # IN international
        "+48 22 555 01 99",       # PL international
        "#1 (555) 0114",          # OCR-damaged '#' for '+'
    ]
    for s in samples:
        text = f"Call {s} now."
        res = phones(text)
        assert len(res) == 1, f"Expected 1 phone match for {s!r}, got {res}"
        c = res[0]
        assert c.pii_type is PIIType.PHONE
        assert text[c.start:c.end] == s


def test_phone_negatives_rejected():
    negatives = [
        "Total amount $1,204.00 paid.",
        "Effective date 2026-04-17 and 17/04/2026.",
        "Published in 2024.",
        "Incident INC-2026-00412 and order ORD-2024-482913.",
        "SSN 123-45-6789.",
    ]
    for neg in negatives:
        assert phones(neg) == [], f"Should not match phone in: {neg!r}"


def test_phone_boundaries_and_deduplication():
    # Start/end of string and inside parentheses
    s = "+1 (555) 0114"
    res = phones(s)
    assert len(res) == 1 and (res[0].start, res[0].end) == (0, len(s))

    s_paren = "Reachable at (+44 20 7946 0958) daily"
    res_p = phones(s_paren)
    assert len(res_p) == 1
    assert s_paren[res_p[0].start:res_p[0].end] == "+44 20 7946 0958"

