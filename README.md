# Optiv Case Study 2 — Text Analysis and PII Detection

Local-first pipeline that finds and redacts PII in PDF (incl. scanned, via OCR), DOCX and PPTX.
Nothing is sent to cloud services or external APIs. This is a case-study prototype: detection
is not perfect and it is not production-ready.

## Pipeline

```
ingest.load -> extract.extract -> detect.detect -> redact.redact
            -> extract + detect on the redacted output (residual scan)
            -> gate.decide -> report.write_report
```

| Module | Purpose |
|---|---|
| `pii_pipeline/models.py` | Shared data contracts (`SourceDocument`, `TextUnit`, `ExtractedDocument`, `Finding`, `RedactionResult`, `GateDecision`) |
| `ingest.py` | Validate file type/size, hash bytes |
| `extract.py` | Native text extraction; OCR for scanned PDF pages |
| `detect.py` | PII findings with type, location, char span, detector, confidence, action |
| `redact.py` | Redacted copy in the original format + audit mapping (gitignored) |
| `report.py` | JSON report without PII values |
| `evaluate.py` | Precision/recall against labelled synthetic data |
| `gate.py` | Release gate; **fails closed** on incomplete extraction, unreadable units, review findings, or residual PII |
| `package.py` | DOCX package scan and sanitising: every XML part is parsed, checked and re-serialised |
| `cli.py` | `python -m pii_pipeline.cli INPUT --out DIR [--audit]`; exit 0 = released, 1 = blocked, 2 = error |

## Status and limitations

Prototype for a case study. **Only DOCX is implemented. PDF (including scanned-PDF OCR) and PPTX
are not implemented yet.** Nothing here is production-ready, and detection is not complete.

**Threat model.** Aimed at accidental PII in ordinary Office documents. Deliberately obfuscated PII
(e.g. `jane (at) example dot com`, zero-width or look-alike characters) and hand-crafted,
schema-invalid packages are out of scope.

**Detected (pattern-based only, no NER):** email, phone (NANP and `+` international), payment card
(Luhn-checked; card-shaped without Luhn goes to review), US SSN, IPv4/IPv6. Unclaimed runs of 7+
digits go to human review. **Not detected:** person names, postal addresses, dates of birth,
Aadhaar, PAN, IBAN.

**Fails closed (release blocked, no redacted file kept)** when the DOCX contains: images, drawings
or text boxes; embedded/binary parts; a non-standard part with more than one text/attribute field;
text nodes with nested elements; a DOCTYPE; any review finding; or residual PII after redaction.
Legacy `.doc` and encrypted files are rejected. Most real documents contain images, so most will be
blocked until OCR/image handling exists.

**Metadata changes on release:** core properties reduced to created/modified dates; app properties
reduced to numeric statistics and flags; custom properties, `word/people.xml` and the thumbnail
removed; document variables removed; tracked-change/comment authors replaced with `Author`.

**Known gaps (crafted, schema-invalid XML; not fixed):**
1. A value split across two attributes of an element in a standard Word part
   (e.g. `<w:p w:foo="jane.te" w:bar="ster@example.com">`) can pass with a fragment left behind.
2. A custom part that declares the Office bibliography root namespace is treated as a known part,
   so the same attribute split can pass there.

**Untested:** opening outputs in Microsoft Word or LibreOffice (only python-docx reopening and ZIP
integrity are verified); real Word-authored documents (tests use generated synthetic fixtures);
detection precision/recall (`evaluate.py` is not implemented, no labelled dataset yet).

## Setup (Windows)

```powershell
py -3.12 -m venv .venv          # 3.11+ works; 3.12 is what was verified
.venv\Scripts\python -m pip install -r requirements.txt
```

### Tesseract OCR (needed for scanned PDFs only)

1. `winget install --id UB-Mannheim.TesseractOCR` (or download the installer from
   https://github.com/UB-Mannheim/tesseract/wiki).
2. Add `C:\Program Files\Tesseract-OCR` to your user `PATH`, then open a new terminal.
3. Check: `tesseract --version`.

## Test

```powershell
.venv\Scripts\python -m pytest -q
```

## Data handling rules

- Never commit real documents. `data/`, `output/`, `audit/` and `*.audit.json` are gitignored.
- Test fixtures must be synthetic and generated in tests (e.g. into `tmp_path`).
- Do not log `TextUnit.text` or PII values; log `doc_id`, `unit_id`, spans and types only.

## File ownership

- Orchestrator: `models.py`, `gate.py`, `requirements.txt`, `.gitignore`, `README.md`,
  `tests/test_gate.py`, `tests/test_models.py`. Changes to contracts go through review.
- Builder: `ingest.py`, `extract.py`, `detect.py`, `redact.py`, `report.py`, `evaluate.py`,
  their tests (`tests/test_<module>.py`), and a future `cli.py`.
