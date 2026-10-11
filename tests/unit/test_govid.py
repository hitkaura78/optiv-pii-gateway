"""Unit tests for government IDs and payment cards (Task T1.4 / Spec U-08, U-09)."""
from detect.recognizers.govid import (
    is_valid_ssn_parts,
    recognize_govids,
    validate_pesel_checksum,
)
from pii_pipeline.models import PIIType


def test_us_ssn_accepts_900_area_and_rejects_invalid_parts():
    # Guide H5: area 900-999 must be accepted; 000, 666, group 00, serial 0000 rejected
    assert is_valid_ssn_parts("912", "34", "5678")
    assert is_valid_ssn_parts("123", "45", "6789")
    assert not is_valid_ssn_parts("000", "12", "3456")
    assert not is_valid_ssn_parts("666", "12", "3456")
    assert not is_valid_ssn_parts("123", "00", "4567")
    assert not is_valid_ssn_parts("123", "45", "0000")

    text = "Valid SSNs: 123-45-6789 and 912-34-5678. Invalid: 000-12-3456, 666-12-3456, 123-00-4567, 123-45-0000."
    res = [c for c in recognize_govids(text) if c.pii_type is PIIType.US_SSN]
    assert [text[c.start:c.end] for c in res] == ["123-45-6789", "912-34-5678"]
    assert all(c.severity == "HIGH" and c.validated for c in res)


def test_in_pan_recognition():
    text = "Indian PAN card ABCDE1234F vs invalid ABCD12345F or abcde1234f."
    res = [c for c in recognize_govids(text) if c.entity_type == "IN_PAN"]
    assert len(res) == 1
    assert text[res[0].start:res[0].end] == "ABCDE1234F"
    assert res[0].pii_type is PIIType.PAN
    assert res[0].severity == "HIGH"


def test_pl_pesel_checksum_and_masked_form():
    # 44051401359 is the classic official valid PESEL specimen (14 May 1944, checksum 9)
    assert validate_pesel_checksum("44051401359")
    assert not validate_pesel_checksum("44051401358")

    text = "Valid PESEL 44051401359, invalid PESEL 44051401358, and masked 850714***** or 910228xxxxx."
    res = recognize_govids(text)
    by_span = {text[c.start:c.end]: c for c in res}

    assert "44051401359" in by_span
    assert by_span["44051401359"].validator == "pesel_checksum:pass"
    assert by_span["44051401359"].score > by_span["44051401358"].score
    assert by_span["44051401358"].validator == "pesel_checksum:fail"

    assert "850714*****" in by_span and by_span["850714*****"].entity_type == "PL_PESEL_MASKED"
    assert by_span["850714*****"].severity == "MEDIUM"
    assert "910228xxxxx" in by_span and by_span["910228xxxxx"].entity_type == "PL_PESEL_MASKED"


def test_passport_and_credit_card():
    text = "Passport No: Z4829104 and card 4111 1111 1111 1111 vs bad card 4111 1111 1111 1112."
    res = recognize_govids(text)
    by_span = {text[c.start:c.end]: c for c in res}
    assert by_span["Z4829104"].entity_type == "PASSPORT"
    assert by_span["4111 1111 1111 1111"].validator == "luhn:pass"
    assert by_span["4111 1111 1111 1112"].validator == "luhn:fail"

