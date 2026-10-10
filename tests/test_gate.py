from pathlib import Path

from pii_pipeline.gate import decide
from pii_pipeline.models import (
    Action, ExtractedDocument, Finding, PIIType, SourceDocument, SourceKind, TextUnit, UnitStatus,
)

SRC = SourceDocument(Path("x.docx"), SourceKind.DOCX, "abc")


def doc(*statuses, complete=True):
    return ExtractedDocument(SRC, [TextUnit(f"para:{i}", "t", status=s) for i, s in enumerate(statuses)], complete)


def finding(action=Action.REDACT):
    return Finding(PIIType.EMAIL, "para:0", 0, 1, "test", 0.9, action)


def test_passes_when_clean():
    assert decide(doc(UnitStatus.OK), [finding()], doc(UnitStatus.OK), []).passed


def test_fails_on_incomplete_extraction():
    d = decide(doc(UnitStatus.OK, complete=False), [], doc(UnitStatus.OK), [])
    assert not d.passed and "incomplete" in d.reasons[0]


def test_fails_on_unreadable_or_partial_unit():
    for s in (UnitStatus.UNREADABLE, UnitStatus.PARTIAL):
        assert not decide(doc(UnitStatus.OK, s), [], doc(UnitStatus.OK), []).passed


def test_fails_on_empty_document():
    assert not decide(doc(), [], doc(UnitStatus.OK), []).passed


def test_fails_on_review_finding():
    assert not decide(doc(UnitStatus.OK), [finding(Action.REVIEW)], doc(UnitStatus.OK), []).passed


def test_fails_on_residual_pii():
    assert not decide(doc(UnitStatus.OK), [], doc(UnitStatus.OK), [finding()]).passed


def test_fails_when_output_not_rescanned():
    assert not decide(doc(UnitStatus.OK), [], None, None).passed


def test_fails_when_output_unreadable():
    assert not decide(doc(UnitStatus.OK), [], doc(UnitStatus.UNREADABLE), []).passed
