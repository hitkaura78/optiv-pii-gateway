"""Unit tests for phone recognizer across regions + lenient fallback (Task T1.3 / Spec U-07)."""
from detect.recognizers.phone import phones
from pii_pipeline.models import PIIType


def test_seven_plus_distinct_phone_formats_across_regions_and_lenient_h7():
    samples = [
        "+1 (555) 0114",          # H7: 8 digits total (+1 + 555 + 0114) via lenient fallback
        "415-555-0132",           # US NANP dashed
        "(212) 555-0199",         # US NANP parens
        "+44 20 7946 0958",       # GB international
        "+91 98765 43210",        # IN international
        "+33 1 42 68 55 00",      # FR international
        "+48 22 555 01 99",       # PL international
        "+65 6555 0199",          # SG international
        "+46 8 555 01 99",        # SE international
        "+852 2555 0199",         # HK international
        "#1 (555) 0114",          # OCR-damaged '#' for '+'
    ]
    for s in samples:
        text = f"Call {s} now."
        res = phones(text)
        assert len(res) == 1, f"Expected 1 deduplicated phone match for {s!r}, got {res}"
        c = res[0]
        assert c.pii_type is PIIType.PHONE
        assert text[c.start:c.end] == s


def test_phone_negatives_rejected():
    negatives = [
        "$1,204.00",
        "Total amount $1,204.00 paid.",
        "2026-04-17",
        "Effective date 2026-04-17 and 17/04/2026.",
        "Published in 2024.",
        "Incident INC-2026-00412 and order ORD-2024-482913.",
        "SSN 123-45-6789.",
    ]
    for neg in negatives:
        assert phones(neg) == [], f"Should not match phone in: {neg!r}"


def test_phone_boundaries_deduplication_and_determinism():
    # Start/end of string
    s = "+1 (555) 0114"
    res = phones(s)
    assert len(res) == 1 and (res[0].start, res[0].end) == (0, len(s))

    # Inside parentheses and multiple phones in one string without duplicates
    multi = "Reachable at (+44 20 7946 0958) or +1 (555) 0114 daily."
    r1 = phones(multi, regions=["US", "GB", "IN", "FR", "PL", "SG", "SE", "HK"])
    r2 = phones(multi, regions=["US", "GB", "IN", "FR", "PL", "SG", "SE", "HK"])
    assert r1 == r2
    assert [multi[c.start:c.end] for c in r1] == ["+44 20 7946 0958", "+1 (555) 0114"]


def test_r01_bare_10_11_digit_runs_without_formatting_or_phone_cue_rejected():
    assert phones("Account ref 0012345678 or PESEL 02271401359") == []
    # With a phone cue nearby, a bare 10-11 digit number IS matched as a phone
    cued = "Call 44051401359 or tel 02079460958"
    res = phones(cued)
    assert [cued[c.start:c.end] for c in res] == ["44051401359", "02079460958"]


def test_look_alikes_ip_isbn_and_version_rejected_h9():
    assert phones("Server 203.0.113.42 restarted") == []
    assert phones("ISBN 978-0-306-40615-7 cited") == []       # valid ISBN-13 check digit
    assert phones("Build v9.36.0.4943 shipped") == []
    # an OCR-glued number is still a phone: only a "v" + dotted version is rejected
    for text, number in (("Call5551234567", "5551234567"), ("Call555-123-4567", "555-123-4567")):
        assert [text[c.start:c.end] for c in phones(text)] == [number]


def test_r02_trailing_decimal_and_currency_code_rejected():
    assert phones(" Wire transfer: 01 234 567,89 PLN ") == []
    assert phones("Amount 01 234 567.00 USD approved") == []
    assert phones("Invoice 01 234 567 EUR paid") == []




