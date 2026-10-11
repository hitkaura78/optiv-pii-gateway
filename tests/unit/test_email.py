"""Unit tests for email recognizer (Task T1.1 / Spec U-06)."""
from detect.recognizers.email import recognize_emails
from pii_pipeline.models import PIIType


def test_valid_standard_email():
    text = "Reach out to priya.nair+ops@example.com for details."
    res = recognize_emails(text)
    assert len(res) == 1
    c = res[0]
    assert c.pii_type is PIIType.EMAIL
    assert c.entity_type == "EMAIL_ADDRESS"
    assert text[c.start:c.end] == "priya.nair+ops@example.com"
    assert c.validator == "syntax:email"


def test_ocr_damaged_email_domains_matched():
    # OCR errors such as '0' for 'o' or '1' for 'l' in the domain/TLD must still match
    text = "Scanned contacts: jane.doe@examp1e.c0m and alex_r@sub.corp-internal.0rg."
    res = recognize_emails(text)
    matched = [text[c.start:c.end] for c in res]
    assert matched == ["jane.doe@examp1e.c0m", "alex_r@sub.corp-internal.0rg"]
    assert all(c.validator == "syntax:email_ocr_domain" for c in res)


def test_rejects_spaces_and_invalid_syntax():
    for invalid in (
        "a @ b",
        "jane.doe @ example.com",
        "jane.doe@ example.com",
        "jane..doe@example.com",
        "@example.com",
        "jane@",
        "user@123.456",
    ):
        assert recognize_emails(invalid) == [], f"Should reject: {invalid!r}"


def test_boundaries_and_trailing_punctuation():
    # Start and end of string
    s1 = "first.last@example.org"
    res1 = recognize_emails(s1)
    assert len(res1) == 1 and (res1[0].start, res1[0].end) == (0, len(s1))

    # Trailing period, comma, parenthesis, semicolon
    s2 = "(bob.smith@example.com), alice.jones@example.org; end@example.com."
    res2 = recognize_emails(s2)
    assert [s2[c.start:c.end] for c in res2] == [
        "bob.smith@example.com",
        "alice.jones@example.org",
        "end@example.com",
    ]

