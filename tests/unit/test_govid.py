"""Unit tests for government IDs and payment cards (Task T1.4 / Spec U-08, U-09)."""
import pytest

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
    # 44051401359 is the official valid PESEL specimen (validated via python-stdnum)
    assert validate_pesel_checksum("44051401359")
    assert not validate_pesel_checksum("44051401358")

    text = "Valid PESEL 44051401359, invalid PESEL 44051401358, and masked 123456xxxxx or 850714*****."
    res = recognize_govids(text)
    by_span = {text[c.start:c.end]: c for c in res}

    assert "44051401359" in by_span
    assert by_span["44051401359"].validator == "pesel_checksum:pass"
    assert by_span["44051401359"].score > by_span["44051401358"].score
    assert by_span["44051401358"].validator == "pesel_checksum:fail"

    assert "123456xxxxx" in by_span and by_span["123456xxxxx"].entity_type == "PL_PESEL_MASKED"
    assert by_span["123456xxxxx"].severity == "MEDIUM"
    assert "850714*****" in by_span and by_span["850714*****"].entity_type == "PL_PESEL_MASKED"


def test_credit_card_luhn():
    text = "Card 4111 1111 1111 1111 vs bad card 4111 1111 1111 1112."
    res = recognize_govids(text)
    by_span = {text[c.start:c.end]: c for c in res}
    assert by_span["4111 1111 1111 1111"].validator == "luhn:pass"
    assert by_span["4111 1111 1111 1112"].validator == "luhn:fail"


@pytest.mark.skip(reason="TODO: PASSPORT and TAX_ID formats not yet specified in Guide/samples [VERIFY BEFORE EXECUTION]")
def test_passport_and_tax_id_formats_todo():
    raise NotImplementedError


def test_r03_ssn_context_connecting_words_and_sentence_boundary():
    for sample in ("SSN is 912345678", "SSN: #912345678", "social security no. 912345678"):
        res = [c for c in recognize_govids(sample) if c.pii_type is PIIType.US_SSN]
        assert len(res) == 1, f"Expected 1 US_SSN in {sample!r}, got {res}"
        assert sample[res[0].start:res[0].end] == "912345678"

    # Negative: digits in another sentence or beyond the ~15 non-digit limit must not match
    assert [c for c in recognize_govids("Missing SSN. Ref 912345678") if c.pii_type is PIIType.US_SSN] == []
    assert [
        c for c in recognize_govids("SSN was not provided for account ref 912345678")
        if c.pii_type is PIIType.US_SSN
    ] == []


def test_r04_pesel_suppressed_on_phone_cue_and_no_double_counting():
    from detect.recognizers.phone import phones

    # Phone cue before 11-digit number -> NOT PL_PESEL, handled by phone recognizer
    assert [c for c in recognize_govids("Call 44051401359") if c.entity_type == "PL_PESEL"] == []
    assert len(phones("Call 44051401359")) == 1

    # Non-phone context -> still PL_PESEL, and not emitted as phone
    id_res = [c for c in recognize_govids("ID 44051401359") if c.entity_type == "PL_PESEL"]
    assert len(id_res) == 1
    assert "ID 44051401359"[id_res[0].start:id_res[0].end] == "44051401359"
    assert phones("ID 44051401359") == []

    # When both recognizers run together, the same span never appears in both outputs
    combined = "Call 44051401359 or check ID 44051401359 and SSN 912-34-5678 or tel: 02271401359."
    phone_spans = {(c.start, c.end) for c in phones(combined)}
    govid_spans = {(c.start, c.end) for c in recognize_govids(combined)}
    assert phone_spans.isdisjoint(govid_spans), f"Overlapping spans: {phone_spans & govid_spans}"


def test_r06_empty_start_and_end_of_string_boundaries():
    assert recognize_govids("") == []

    start_text = "912-34-5678 on file"
    res_start = recognize_govids(start_text)
    assert len(res_start) == 1
    assert res_start[0].start == 0
    assert start_text[res_start[0].start:res_start[0].end] == "912-34-5678"

    end_text = "PAN on file is ABCDE1234F"
    res_end = recognize_govids(end_text)
    assert len(res_end) == 1
    assert res_end[0].end == len(end_text)
    assert end_text[res_end[0].start:res_end[0].end] == "ABCDE1234F"


