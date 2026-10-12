"""CLI: python -m pii_pipeline.cli INPUT --out DIR [--audit]. Exit 0 only if the gate passes.

Exit codes: 0 gate passed, 1 gate failed (no output file is kept), 2 error."""
import argparse
import os
import sys
from pathlib import Path

from . import detect, extract, gate, ingest, redact, report


def _unlink(path) -> None:
    try:
        if path is not None:
            Path(path).unlink()
    except FileNotFoundError:
        pass


def run(input_path: Path, out_dir: Path, write_audit: bool = False):
    """Returns (decision, result, report_path, source). result.output_path is the released
    file only if decision.passed; otherwise it names a draft that has been deleted."""
    out_dir = Path(out_dir)
    src = ingest.load(input_path)
    stem = src.doc_id[:12]
    ext = src.kind.value                # "docx" / "pdf": same naming rules for every format
    final = out_dir / f"{stem}.redacted.{ext}"
    for stale in (final, out_dir / f"{stem}.UNSAFE-DRAFT.{ext}", out_dir / f"{stem}.audit.json"):
        _unlink(stale)                  # nothing from an earlier attempt for this document survives
    result = None
    released = False
    try:
        doc = extract.extract(src)
        findings = detect.detect(doc)
        result = redact.redact(doc, findings, out_dir, write_audit=write_audit)
        red_doc = extract.extract(ingest.load(result.output_path))
        residual = detect.detect_residual(red_doc)
        decision = gate.decide(doc, findings, red_doc, residual)
        rpath = report.write_report(doc, findings, decision, out_dir / f"{stem}.report.json")
        if decision.passed:
            os.replace(result.output_path, final)
            result.output_path = final
            released = True
        return decision, result, rpath, src
    finally:
        if not released and result is not None:
            _unlink(result.output_path)         # failed or interrupted: no draft survives
            _unlink(result.audit_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pii_pipeline")
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--audit", action="store_true", help="also write the sensitive audit mapping (only kept on PASS)")
    args = ap.parse_args(argv)
    try:
        decision, result, rpath, src = run(args.input, args.out, args.audit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:      # exc text can contain filesystem paths: report the class only
        print(f"error: file system operation failed ({type(exc).__name__})", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: unexpected failure ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"doc_id={src.doc_id[:12]} gate={'PASS' if decision.passed else 'FAIL'}")
    for r in decision.reasons:
        print(f"  - {r}")
    print(f"report={rpath}")
    if decision.passed:
        print(f"redacted={result.output_path}")
    else:
        print("NOT SAFE FOR RELEASE: no redacted file was kept")
    return 0 if decision.passed else 1


if __name__ == "__main__":
    sys.exit(main())
