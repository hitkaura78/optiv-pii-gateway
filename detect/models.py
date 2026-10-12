"""Data models for the Member 3 PII detection engine.

Extends pii_pipeline.models.Finding via subclassing so every DetectedFinding is
a drop-in Finding for pii_pipeline.gate, redact, report, and evaluate, while
also carrying full Member 3 traceability metadata (detectors, validator, reason,
entity_key, entity_type_tag, category, severity, doc_id).

Rule: no model stores raw PII text. Only character offsets (start, end) are kept.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from pii_pipeline.models import Action, Finding, PIIType


@dataclass(frozen=True)
class Candidate:
    """One candidate span emitted by a recognizer, context rule, NER, or registry."""
    start: int
    end: int
    pii_type: PIIType
    entity_type: str
    score: float
    validator: str
    detector: str
    category: str = "CONTACT"
    severity: str = "MEDIUM"
    reason: str = ""
    entity_key: str = ""
    validated: bool = False


@dataclass(frozen=True)
class DetectedFinding(Finding):
    """Schema-valid Finding with Member 3 traceability fields.

    Because DetectedFinding subclasses pii_pipeline.models.Finding, it works
    seamlessly with pii_pipeline.gate.decide, pii_pipeline.redact.redact,
    pii_pipeline.report.write_report, and pii_pipeline.evaluate.score.
    """
    detectors: tuple[str, ...] = ()
    validator: str = "none"
    reason: str = ""
    entity_key: str = ""
    entity_type_tag: str = ""
    category: str = ""
    severity: str = "MEDIUM"
    doc_id: str = ""

    @property
    def block_id(self) -> str:
        return self.unit_id

    @property
    def score(self) -> float:
        return self.confidence

    @property
    def entity_type(self) -> str:
        return self.entity_type_tag.strip("[]").rsplit("_", 1)[0] if self.entity_type_tag else self.pii_type.value.upper()

    @property
    def action_label(self) -> str:
        if self.action is Action.KEEP:
            return "ALLOW"
        return self.action.value.upper()

    def model_dump(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "doc_id": self.doc_id,
            "unit_id": self.unit_id,
            "block_id": self.unit_id,
            "pii_type": self.pii_type.value,
            "start": self.start,
            "end": self.end,
            "detector": self.detector,
            "detectors": list(self.detectors) if self.detectors else [self.detector],
            "validator": self.validator,
            "confidence": self.confidence,
            "score": self.confidence,
            "action": self.action.value,
            "action_label": self.action_label,
            "reason": self.reason,
            "entity_key": self.entity_key,
            "entity_type_tag": self.entity_type_tag,
            "category": self.category,
            "severity": self.severity,
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True)

    @classmethod
    def model_validate(cls, obj: dict | DetectedFinding) -> DetectedFinding:
        if isinstance(obj, cls):
            return obj
        if isinstance(obj, Finding):
            return cls(
                pii_type=obj.pii_type,
                unit_id=obj.unit_id,
                start=obj.start,
                end=obj.end,
                detector=obj.detector,
                confidence=obj.confidence,
                action=obj.action,
                finding_id=obj.finding_id,
                detectors=(obj.detector,),
            )
        if not isinstance(obj, dict):
            raise ValueError("Expected dict or Finding instance")
        pii_type = obj.get("pii_type")
        if isinstance(pii_type, str):
            pii_type = PIIType(pii_type)
        action = obj.get("action")
        if isinstance(action, str):
            if action.upper() == "ALLOW":
                action = Action.KEEP
            else:
                action = Action(action.lower())
        detectors = tuple(obj.get("detectors") or ([obj["detector"]] if "detector" in obj else ()))
        return cls(
            pii_type=pii_type,
            unit_id=str(obj.get("unit_id") or obj.get("block_id") or ""),
            start=obj.get("start"),
            end=obj.get("end"),
            detector=str(obj.get("detector") or (detectors[0] if detectors else "unknown")),
            confidence=float(obj.get("confidence", obj.get("score", 0.0))),
            action=action,
            finding_id=str(obj.get("finding_id") or uuid.uuid4().hex[:12]),
            detectors=detectors,
            validator=str(obj.get("validator", "none")),
            reason=str(obj.get("reason", "")),
            entity_key=str(obj.get("entity_key", "")),
            entity_type_tag=str(obj.get("entity_type_tag", "")),
            category=str(obj.get("category", "")),
            severity=str(obj.get("severity", "MEDIUM")),
            doc_id=str(obj.get("doc_id", "")),
        )

