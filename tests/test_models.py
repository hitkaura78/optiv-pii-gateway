from pathlib import Path

from pii_pipeline.models import ExtractedDocument, SourceDocument, SourceKind, TextUnit


def test_repr_does_not_leak_text():
    secret = "jane.doe@example.com"
    d = ExtractedDocument(SourceDocument(Path("a.pdf"), SourceKind.PDF, "h"), [TextUnit("page:1", secret)], True)
    assert secret not in repr(d)


def test_dependencies_import():
    import docx, fitz, pptx, pytesseract, PIL  # noqa: F401
