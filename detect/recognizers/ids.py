"""Employee, director, and vendor-worker ID recognizer (Task T1.2 / U-10).

All patterns are loaded dynamically from `cfg.detect.id_formats` (YAML-configured).
Reference IDs (INC-, LOS-, RSK-, CTL-, GRP-, SPK-, ISS-) are never matched as PII here;
they are handled by the allow-list layer with explicit reason codes.
"""
from __future__ import annotations

import re
from typing import Any

from detect.config import load_config
from detect.models import Candidate
from pii_pipeline.models import PIIType


def recognize_ids(text: str, cfg: Any = None) -> list[Candidate]:
    """Recognize structured employee/director/vendor IDs defined in cfg.detect.id_formats."""
    if not text:
        return []
    resolved_cfg = load_config(cfg)
    id_formats = resolved_cfg.detect.id_formats or {}
    out: list[Candidate] = []

    for entity_type, pattern in sorted(id_formats.items()):
        if not pattern:
            continue
        bounded = re.compile(rf"(?<![A-Za-z0-9-])({pattern})(?![A-Za-z0-9-])")
        for m in bounded.finditer(text):
            s, e = m.span(1)
            matched = text[s:e]
            out.append(
                Candidate(
                    start=s,
                    end=e,
                    pii_type=PIIType.OTHER,
                    entity_type=entity_type,
                    score=0.92,
                    validator=f"format:{entity_type.lower()}",
                    detector=f"regex:{entity_type.lower()}",
                    category="EMPLOYEE_IDENTIFIER",
                    severity="MEDIUM",
                    entity_key=matched,
                    validated=True,
                )
            )
    out.sort(key=lambda c: (c.start, -(c.end - c.start), c.entity_type))
    return out

