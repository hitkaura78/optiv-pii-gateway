"""Run report (JSON). Owner: builder."""
from pathlib import Path

from .models import ExtractedDocument, Finding, GateDecision


def write_report(doc: ExtractedDocument, findings: list[Finding], decision: GateDecision, out_path: Path) -> Path:
    """Counts by type/action, locations, gate result. Never include PII values."""
    raise NotImplementedError
