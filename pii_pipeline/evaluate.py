"""Evaluation against labelled synthetic data. Owner: builder."""
from .models import Finding


def score(predicted: list[Finding], gold: list[Finding]) -> dict:
    """Precision/recall/F1 per PIIType, matching on (unit_id, overlapping span, pii_type)."""
    raise NotImplementedError
