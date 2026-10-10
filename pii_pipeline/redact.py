"""Redaction of the original file format. Owner: builder.

DOCX only. Findings with Action.REDACT/MASK are replaced by a typed placeholder such as
[EMAIL]; REVIEW/KEEP findings are left untouched (the gate blocks REVIEW).
"""
import hashlib
import io
import json
import zipfile
from pathlib import Path

from . import package
from .extract import scan_package
from .models import Action, ExtractedDocument, Finding, RedactionResult, SourceKind


def _merge_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Sort and merge overlapping spans (defensive; detect() already de-overlaps)."""
    out: list[list] = []
    for s, e, label in sorted(spans):
        if out and s < out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e, label])
    return [tuple(x) for x in out]


def redact(doc: ExtractedDocument, findings: list[Finding], out_dir: Path, write_audit: bool = False) -> RedactionResult:
    """Write a redacted copy (same format) to out_dir. PDF redaction must remove the
    underlying text, not just draw a box. Write the audit mapping only under out_dir.

    The output is an UNVERIFIED draft named <doc_id12>.UNSAFE-DRAFT.docx; the caller must
    re-scan it, pass the gate, and only then rename it. The audit mapping (finding_id ->
    original value) is sensitive and is written only when write_audit=True, to
    <out_dir>/<doc_id12>.audit.json (gitignored). Default: not written.

    Findings located in parts that are deliberately removed (custom properties, people list)
    count as applied: the whole part is deleted."""
    if doc.source.kind is SourceKind.PDF:
        from .pdf_redact import redact_pdf
        return redact_pdf(doc, findings, out_dir, write_audit)
    if doc.source.kind is not SourceKind.DOCX:
        raise ValueError("only DOCX and PDF redaction are implemented")
    data = doc.source.path.read_bytes()      # process exactly the bytes we hash
    if hashlib.sha256(data).hexdigest() != doc.source.doc_id:
        raise ValueError("source file changed since extraction")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sc = scan_package(data)
    units = {u.unit_id: u for u in sc.units}
    expected = {u.unit_id: u.text for u in doc.units}
    if set(expected) != set(units) or any(units[k].text != v for k, v in expected.items()):
        raise ValueError("source content does not match extraction")

    spans_by_unit: dict[str, list] = {}
    audit: dict[str, str] = {}
    applied: list[str] = []
    for f in findings:
        if f.action not in (Action.REDACT, Action.MASK):
            continue
        text = expected[f.unit_id]
        s, e = (0, len(text)) if f.start is None else (f.start, f.end)
        spans_by_unit.setdefault(f.unit_id, []).append((s, e, f"[{f.pii_type.name}]"))
        audit[f.finding_id] = text[s:e]
        applied.append(f.finding_id)

    removed = {n for n in sc.order if package.should_remove(n)}
    touched: set[str] = set()
    for uid, spans in spans_by_unit.items():
        unit = units[uid]
        if unit.part in removed:        # part is deleted below: explicitly accounted for
            continue
        package.apply_spans(unit, _merge_spans(spans))
        touched.add(unit.part)

    with zipfile.ZipFile(io.BytesIO(data)) as zin:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in sc.order:
                if name in removed:
                    continue
                root = sc.roots.get(name)
                if root is not None:    # every XML part is written from its checked tree, never copied
                    package.finalize_part(name, root, removed, name in touched)
                    blob = package.serialize(root)
                else:
                    blob = zin.read(name)    # non-XML parts: extraction is already incomplete for these
                zout.writestr(name, blob)

    # verify the saved package, not an in-memory view
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as zf:
        if zf.testzip() is not None:
            raise ValueError("redacted package failed integrity check")
        package.verify_scrubbed(zf.namelist(), zf.read)

    stem = doc.source.doc_id[:12]       # never reuse the original filename (may contain PII)
    output_path = out_dir / f"{stem}.UNSAFE-DRAFT.docx"
    output_path.write_bytes(out.getvalue())

    audit_path = None
    if write_audit:
        audit_path = out_dir / f"{stem}.audit.json"
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
    return RedactionResult(output_path=output_path, applied=applied, audit_path=audit_path)
