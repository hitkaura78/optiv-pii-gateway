"""Redaction of the original file format. Owner: builder."""
from pathlib import Path

from .models import ExtractedDocument, Finding, RedactionResult


def redact(doc: ExtractedDocument, findings: list[Finding], out_dir: Path) -> RedactionResult:
    """Write a redacted copy (same format) to out_dir. PDF redaction must remove the
    underlying text, not just draw a box. Write the audit mapping only under out_dir."""
    raise NotImplementedError
