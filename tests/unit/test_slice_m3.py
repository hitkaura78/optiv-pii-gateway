"""Vertical slice tests for Member 3 engine, resolver, allow-list, and strict mode (Task T0.2)."""
from pathlib import Path

from detect.engine import detect_and_resolve, detect_strict
from detect.models import DetectedFinding
from pii_pipeline import gate
from pii_pipeline.models import (
    Action,
    ExtractedDocument,
    PIIType,
    SourceDocument,
    SourceKind,
    TextUnit,
)


def _make_doc(text: str, unit_id: str = "para:0") -> ExtractedDocument:
    src = SourceDocument(Path("synthetic.docx"), SourceKind.DOCX, "a" * 64)
    return ExtractedDocument(src, [TextUnit(unit_id, text)], True)


def test_slice_email_and_allowlist_and_determinism():
    secret_email = "jane.tester@example.com"
    ref_id = "INC-2026-00412"
    text = f"Ticket {ref_id}: please contact {secret_email} immediately."
    doc = _make_doc(text, unit_id="para:0")

    findings_run1 = detect_and_resolve(doc)
    findings_run2 = detect_and_resolve(doc)

    # Determinism across runs
    assert [f.model_dump_json() for f in findings_run1] == [f.model_dump_json() for f in findings_run2]

    # Validate every finding via DetectedFinding.model_validate and check no raw PII leaks
    for f in findings_run1:
        validated = DetectedFinding.model_validate(f.model_dump())
        assert validated == f
        dumped = f.model_dump_json()
        assert secret_email not in dumped
        assert "jane.tester" not in dumped

    email_findings = [f for f in findings_run1 if f.pii_type is PIIType.EMAIL]
    assert len(email_findings) == 1
    ef = email_findings[0]
    assert ef.block_id == "para:0"
    assert ef.action is Action.MASK
    assert text[ef.start:ef.end] == secret_email
    assert "regex:email" in ef.detectors

    allow_findings = [f for f in findings_run1 if f.action is Action.KEEP]
    assert any(f.reason == "ALLOW_REF_ID_INC" and text[f.start:f.end] == ref_id for f in allow_findings)


def test_slice_detect_strict_and_gate_integration():
    secret_email = "residual.leak@example.org"
    text_with_leak = f"Redacted [PERSON_001] for ticket INC-2026-00999 but left {secret_email} here."
    strict_findings = detect_strict(text_with_leak)

    assert len(strict_findings) == 1
    sf = strict_findings[0]
    assert sf.pii_type is PIIType.EMAIL
    assert sf.action is Action.MASK
    assert sf.doc_id == "strict" and sf.block_id == "strict"
    assert text_with_leak[sf.start:sf.end] == secret_email
    assert secret_email not in sf.model_dump_json()

    # Verify compatibility with Member 1's gate (`pii_pipeline.gate.decide`)
    orig_doc = _make_doc(f"Contact {secret_email}")
    clean_redacted_doc = _make_doc("Contact [EMAIL_001] re INC-2026-00999")
    clean_residual = detect_strict(clean_redacted_doc.units[0].text, include_allow=True)
    decision_pass = gate.decide(orig_doc, detect_and_resolve(orig_doc), clean_redacted_doc, clean_residual)
    assert decision_pass.passed, decision_pass.reasons

    leaky_doc = _make_doc(text_with_leak)
    decision_fail = gate.decide(orig_doc, detect_and_resolve(orig_doc), leaky_doc, strict_findings)
    assert not decision_fail.passed
    assert any("residual" in r for r in decision_fail.reasons)


def test_slice_ir_block_text_offsets_and_determinism():
    """Verify detect_and_resolve on an ir_min.json-shaped structure (pages -> blocks -> Block.text)."""
    block_text = "Please send the report to jane.tester@example.com before Friday."
    ir_min = {
        "doc_id": "doc_ir_min_001",
        "pages": [
            {
                "page_num": 1,
                "blocks": [
                    {
                        "block_id": "p1_b0",
                        "kind": "paragraph",
                        "text": block_text,
                        "ocr_conf": 0.99,
                    }
                ],
            }
        ],
        "metadata_blocks": [],
    }

    run1 = detect_and_resolve(ir_min, cfg=None)
    run2 = detect_and_resolve(ir_min, cfg=None)

    assert run1 == run2
    assert [f.model_dump_json() for f in run1] == [f.model_dump_json() for f in run2]

    email_findings = [f for f in run1 if f.pii_type is PIIType.EMAIL]
    assert len(email_findings) == 1
    finding = email_findings[0]
    assert finding.action is Action.MASK
    assert finding.block_id == "p1_b0"
    assert block_text[finding.start:finding.end] == "jane.tester@example.com"


