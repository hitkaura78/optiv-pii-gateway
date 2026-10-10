"""Shared data contracts. Owned by the orchestrator: change only via review.

Rule: no contract stores a PII value outside TextUnit.text, and that field is
excluded from repr so findings and documents can be logged safely.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SourceKind(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"


class ExtractionMethod(str, Enum):
    NATIVE = "native"
    OCR = "ocr"


class UnitStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"          # e.g. low OCR confidence, some content skipped
    UNREADABLE = "unreadable"    # could not extract at all


class PIIType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    US_SSN = "us_ssn"
    AADHAAR = "aadhaar"
    PAN = "pan"
    IBAN = "iban"
    IP_ADDRESS = "ip_address"
    DATE_OF_BIRTH = "date_of_birth"
    PERSON = "person"
    ADDRESS = "address"
    OTHER = "other"


class Action(str, Enum):
    REDACT = "redact"   # remove/black out
    MASK = "mask"       # replace with placeholder, e.g. [EMAIL]
    REVIEW = "review"   # human must decide; blocks release
    KEEP = "keep"       # judged not sensitive


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    kind: SourceKind
    doc_id: str          # sha256 of file bytes; use this, not the filename, in logs


@dataclass
class TextUnit:
    """Smallest addressable piece of text in a source.

    unit_id is a stable locator, e.g. "page:3", "para:12", "table:0/r1/c2",
    "slide:2/shape:4/para:0". Page/slide numbers are 1-based.
    """
    unit_id: str
    text: str = field(repr=False)
    method: ExtractionMethod = ExtractionMethod.NATIVE
    status: UnitStatus = UnitStatus.OK
    ocr_confidence: float | None = None   # 0..1, OCR units only


@dataclass
class ExtractedDocument:
    source: SourceDocument
    units: list[TextUnit]
    complete: bool                # False if the extractor knowingly skipped content
    warnings: list[str] = field(default_factory=list)   # must not contain PII


@dataclass(frozen=True)
class Finding:
    """One detected PII occurrence. Holds a location, never the value."""
    pii_type: PIIType
    unit_id: str
    start: int | None          # char span in TextUnit.text; None = whole unit (e.g. image)
    end: int | None
    detector: str              # e.g. "regex:email", "luhn:credit_card"
    confidence: float          # 0..1
    action: Action
    finding_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class RedactionResult:
    output_path: Path
    applied: list[str]         # finding_ids actually redacted/masked
    audit_path: Path | None    # finding_id -> original value; gitignored, never logged


@dataclass
class GateDecision:
    passed: bool
    reasons: list[str]         # why it failed; must not contain PII
