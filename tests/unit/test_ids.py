"""Unit tests for employee, director, and vendor-worker IDs from config (Task T1.2 / Spec U-10)."""
from detect.recognizers.ids import recognize_ids


def test_default_yaml_id_formats():
    text = "Assigned to EMP-10482, reviewed by DIR-8821 and vendor MER-IN-4471."
    res = recognize_ids(text)
    found = [(c.entity_type, text[c.start:c.end]) for c in res]
    assert found == [
        ("EMPLOYEE_ID", "EMP-10482"),
        ("DIRECTOR_ID", "DIR-8821"),
        ("VENDOR_WORKER_ID", "MER-IN-4471"),
    ]


def test_short_or_malformed_ids_rejected():
    text = "Short EMP-123 or EMP-1234 or EMP-123456 or DIR-12 or MER-123-4471 must not match."
    assert recognize_ids(text) == []


def test_reference_ids_not_matched_as_employee_ids():
    text = "Refs: INC-2026-00412, LOS-9910, RSK-102, CTL-008, GRP-11, SPK-04, ISS-7712."
    assert recognize_ids(text) == []


def test_changing_config_pattern_changes_behavior():
    custom_cfg = {
        "detect": {
            "id_formats": {
                "EMPLOYEE_ID": r"STAFF-\d{6}",
                "DIRECTOR_ID": r"EXEC-\d{3}",
                "VENDOR_WORKER_ID": r"VND-[A-Z]{3}-\d{5}",
            }
        }
    }
    text = "Old EMP-10482 ignored; new STAFF-654321, EXEC-901, and VND-USA-12345 matched."
    res = recognize_ids(text, cfg=custom_cfg)
    found = [(c.entity_type, text[c.start:c.end]) for c in res]
    assert found == [
        ("EMPLOYEE_ID", "STAFF-654321"),
        ("DIRECTOR_ID", "EXEC-901"),
        ("VENDOR_WORKER_ID", "VND-USA-12345"),
    ]

