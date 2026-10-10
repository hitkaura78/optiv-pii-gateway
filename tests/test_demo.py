import json

import pytest

from pii_pipeline import demo, pdf_extract

ALL_VALUES = [demo.EMAIL, demo.PHONE, demo.CARD, demo.SSN, demo.IP, demo.LINK_EMAIL, demo.EMAIL2, demo.PHONE2,
              demo.IP2, demo.COMMENT_EMAIL, demo.COMMENT_AUTHOR, demo.EMAIL3, demo.PHONE3, demo.SSN3, demo.CARD3,
              demo.EMAIL4, demo.ORDER]
# fragments that would also betray a value, e.g. the local part of an address or the last digits of a number
FRAGMENTS = ["demo.person", "demo.link", "slides.owner", "comment.reviewer", "pdf.person", "scan.person",
             "555-0142", "555-0188", "555-0166", "482913", "6789", "9876", "4444", "113.42", "100.17"]


def test_demo_runs_and_prints_no_original_values(tmp_path, capsys):
    rc = demo.main(["--out", str(tmp_path)])
    shown = capsys.readouterr()
    text = shown.out + shown.err
    assert rc == 0, text
    for v in ALL_VALUES + FRAGMENTS:
        assert v not in text, v
    assert text.count("gate    : PASS") == (4 if pdf_extract.ocr_available() else 3)
    assert text.count("gate    : FAIL (exit 1)") == 2
    assert "no file kept" in text and "ALL CASES AS EXPECTED" in text
    if not pdf_extract.ocr_available():
        assert "skipped: Tesseract not found" in text
    else:
        assert "mean word confidence=" in text
    assert "0 of" in text and "ppt/comments/comment1.xml" in text and "ppt/commentAuthors.xml" in text
    assert "text layer:" in text


def test_demo_outputs_hold_no_values_and_blocked_cases_leave_no_file(tmp_path):
    assert demo.main(["--out", str(tmp_path)]) == 0
    out = tmp_path / "output"
    released = sorted(p.name for p in out.iterdir() if ".redacted." in p.name)
    assert len(released) == (4 if pdf_extract.ocr_available() else 3)
    assert not list(out.glob("*UNSAFE-DRAFT*")) and not list(out.glob("*.audit.json"))
    assert [p.suffix for p in out.glob("*.redacted.docx")] == [".docx"]       # only case 1; cases 5 and 6 kept nothing
    for rpt in out.glob("*.report.json"):
        text = rpt.read_text(encoding="utf-8")
        assert not any(v in text for v in ALL_VALUES + FRAGMENTS)
        json.loads(text)


def test_demo_is_rerunnable_in_the_same_folder(tmp_path):
    assert demo.main(["--out", str(tmp_path)]) == 0
    assert demo.main(["--out", str(tmp_path)]) == 0
