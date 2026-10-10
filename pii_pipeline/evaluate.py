"""Evaluation against labelled synthetic data. Owner: builder."""
from .models import Action, Finding

_ACTIVE = (Action.REDACT, Action.MASK)


def _overlap(a: Finding, b: Finding) -> int:
    """Characters shared by two spans; a span of None means the whole unit (matches anything)."""
    if a.unit_id != b.unit_id:
        return 0
    if a.start is None or b.start is None:
        return 1
    return max(0, min(a.end, b.end) - max(a.start, b.start))


def _ratios(row: dict) -> None:
    tp, fp, fn = row["tp"], row["fp"], row["fn"]
    row["precision"] = tp / (tp + fp) if tp + fp else None
    row["recall"] = tp / (tp + fn) if tp + fn else None
    p, r = row["precision"], row["recall"]
    row["f1"] = None if p is None or r is None else (0.0 if p + r == 0 else 2 * p * r / (p + r))


def _empty() -> dict:
    return {"tp": 0, "fp": 0, "fn": 0, "review": 0, "review_caught": 0}


def score(predicted: list[Finding], gold: list[Finding]) -> dict:
    """Precision/recall/F1 per PIIType, matching on (unit_id, overlapping span, pii_type).

    * Only REDACT/MASK predictions can be true or false positives. Each prediction matches at most
      one gold item and vice versa (greedy, largest overlap first), so a duplicate prediction of the
      same gold item is a false positive.
    * REVIEW predictions are a separate column ("review", by predicted type) and are never true
      positives. "review_caught" counts gold items that were NOT auto-detected but overlap a REVIEW
      finding (flagged for a human instead of missed silently).
    * KEEP predictions are ignored.
    * precision/recall/f1 are None when undefined (no predictions / no gold items).

    Returns {"per_type": {type_value: row}, "overall": row} (overall is micro-averaged)."""
    active = [p for p in predicted if p.action in _ACTIVE]
    reviews = [p for p in predicted if p.action is Action.REVIEW]
    per: dict[str, dict] = {}

    def row(t) -> dict:
        return per.setdefault(t.value, _empty())

    used: set[int] = set()
    for g in sorted(gold, key=lambda x: (x.unit_id, -1 if x.start is None else x.start)):
        best, best_ov = None, 0
        for i, p in enumerate(active):
            if i in used or p.pii_type is not g.pii_type:
                continue
            ov = _overlap(p, g)
            if ov > best_ov:
                best, best_ov = i, ov
        if best is None:
            row(g.pii_type)["fn"] += 1
            if any(_overlap(r, g) for r in reviews):
                row(g.pii_type)["review_caught"] += 1
        else:
            used.add(best)
            row(g.pii_type)["tp"] += 1
    for i, p in enumerate(active):
        if i not in used:
            row(p.pii_type)["fp"] += 1
    for r in reviews:
        row(r.pii_type)["review"] += 1

    overall = _empty()
    for r in per.values():
        for k in overall:
            overall[k] += r[k]
        _ratios(r)
    _ratios(overall)
    return {"per_type": per, "overall": overall}
