import json
import re

import pytest

from pii_pipeline import benchmark, pdf_extract


def test_generation_is_deterministic_and_uses_only_reserved_values(tmp_path):
    a = benchmark.generate(tmp_path / "a", seed=7, per_kind=3, kinds=("docx", "pptx", "pdf-native"))
    b = benchmark.generate(tmp_path / "b", seed=7, per_kind=3, kinds=("docx", "pptx", "pdf-native"))
    assert a == b and len(a["files"]) == 9
    assert benchmark.generate(tmp_path / "c", seed=8, per_kind=3, kinds=("docx",))["files"] != a["files"][:3]
    for entry in a["files"]:
        for g in entry["gold"]:
            v = g["value"]
            if g["key"] == "email":
                assert re.search(r"@(?:[\w.]+\.)?example\.(?:com|org)$", v, re.I), v
            if g["key"] == "ip_address":
                assert v.startswith(("203.0.113.", "198.51.100.", "192.0.2.", "2001:db8")), v
            if g["key"] == "phone":
                assert "555" in v or "7946" in v, v
        assert entry["gold"] and entry["decoys"]
        # every supported type is represented somewhere and unsupported types are always present
        assert {"person", "address", "date_of_birth"} <= {g["key"] for g in entry["gold"]}


def test_smoke_run_scores_and_reports(tmp_path):
    benchmark.generate(tmp_path, seed=3, per_kind=2, kinds=("docx", "pptx", "pdf-native"))
    res = benchmark.run_corpus(tmp_path)
    per = res["score"]["per_type"]
    assert per["email"]["recall"] == 1.0 and per["us_ssn"]["recall"] == 1.0           # well-formed values are found
    assert per["person"]["tp"] > 0                                  # names now come from the detect/ engine
    assert res["score"]["overall"]["tp"] > 0
    assert sum(sum(c.values()) for c in res["outcomes"].values()) == 6
    assert res["leaks"].get("email", 0) == 0 and res["leaks"].get("us_ssn", 0) == 0     # nothing supported released
    text = benchmark.format_report(res, 1.0)
    assert "Per type" in text and "Look-alike decoys" in text and "Pipeline outcome per format" in text
    assert not any(g["value"] in text for e in json.loads((tmp_path / "manifest.json").read_text())["files"]
                   for g in e["gold"])           # the report never prints corpus values


def test_cli_entry_point_prints_table(tmp_path, capsys):
    assert benchmark.main(["--out", str(tmp_path), "--per-kind", "1", "--kinds", "docx"]) == 0
    assert "overall" in capsys.readouterr().out
    assert benchmark.main(["--out", str(tmp_path), "--kinds", "nope"]) == 2


@pytest.mark.skipif(not pdf_extract.ocr_available(), reason="Tesseract not found: add it to PATH or set TESSERACT_CMD")
def test_scanned_and_mixed_pdfs_run_through_ocr(tmp_path):
    benchmark.generate(tmp_path, seed=5, per_kind=1, kinds=("pdf-scanned", "pdf-mixed"))
    res = benchmark.run_corpus(tmp_path)
    assert res["score"]["overall"]["tp"] > 0
    assert res["outcomes"]["pdf-scanned"].total() == 1 and res["outcomes"]["pdf-mixed"].total() == 1
