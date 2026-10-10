"""Release gate. Fails closed: any doubt means the document is not released."""
from __future__ import annotations

from .models import Action, ExtractedDocument, Finding, GateDecision, UnitStatus


def decide(
    original: ExtractedDocument,
    findings: list[Finding],
    redacted: ExtractedDocument | None,
    residual: list[Finding] | None,
) -> GateDecision:
    """original/findings: before redaction. redacted/residual: re-extraction and
    re-detection of the redacted output. None means that step did not run -> fail."""
    reasons: list[str] = []

    if not original.complete:
        reasons.append("source extraction incomplete")
    if not original.units:
        reasons.append("source produced no text units")
    bad = [u.unit_id for u in original.units if u.status is not UnitStatus.OK]
    if bad:
        reasons.append(f"{len(bad)} source unit(s) not fully readable: {', '.join(bad)}")

    review = [f.finding_id for f in findings if f.action is Action.REVIEW]
    if review:
        reasons.append(f"{len(review)} finding(s) require human review")

    if redacted is None or residual is None:
        reasons.append("redacted output was not re-scanned")
    else:
        if not redacted.complete or any(u.status is not UnitStatus.OK for u in redacted.units):
            reasons.append("redacted output could not be fully re-extracted")
        live = [f for f in residual if f.action is not Action.KEEP]
        if live:
            reasons.append(f"{len(live)} residual PII finding(s) in redacted output")

    return GateDecision(passed=not reasons, reasons=reasons)
