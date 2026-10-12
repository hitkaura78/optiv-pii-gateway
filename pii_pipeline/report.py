"""Run report (JSON). Owner: builder."""
import json
from collections import Counter
from pathlib import Path

from .models import ExtractedDocument, Finding, GateDecision


# Detection-engine traceability fields, reported when present. None holds a value: the
# entity_key is an opaque per-document key and the tag is a placeholder like [PERSON_001].
_TRACE_FIELDS = ("entity_type", "category", "severity", "reason", "validator", "entity_type_tag", "entity_key")


def _finding_row(f: Finding) -> dict:
    row = {"finding_id": f.finding_id, "type": f.pii_type.value, "unit_id": f.unit_id,
           "start": f.start, "end": f.end, "detector": f.detector,
           "confidence": f.confidence, "action": f.action.value}
    for name in _TRACE_FIELDS:
        if getattr(f, name, None):
            row[name] = getattr(f, name)
    if getattr(f, "detectors", None):
        row["detectors"] = list(f.detectors)
    return row


def write_report(doc: ExtractedDocument, findings: list[Finding], decision: GateDecision, out_path: Path) -> Path:
    """Counts by type/action, locations, gate result. Never include PII values."""
    report = {
        "doc_id": doc.source.doc_id,
        "kind": doc.source.kind.value,
        "extraction_complete": doc.complete,
        "extraction_warnings": list(doc.warnings),
        "unit_count": len(doc.units),
        "counts_by_type": dict(sorted(Counter(f.pii_type.value for f in findings).items())),
        "counts_by_action": dict(sorted(Counter(f.action.value for f in findings).items())),
        "findings": [_finding_row(f) for f in findings],
        "gate": {"passed": decision.passed, "reasons": list(decision.reasons)},
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out_path
