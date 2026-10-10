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
