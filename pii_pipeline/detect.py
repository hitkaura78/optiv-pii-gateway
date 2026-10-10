"""PII detection over extracted text. Owner: builder."""
from .models import ExtractedDocument, Finding


def detect(doc: ExtractedDocument) -> list[Finding]:
    """Return findings with char spans into TextUnit.text. Low-confidence hits -> Action.REVIEW."""
    raise NotImplementedError
