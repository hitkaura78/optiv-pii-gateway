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
| `pdf_extract.py` | PDF native text (PyMuPDF) plus Tesseract OCR for image/vector pages |
| `pdf_redact.py` | PDF true redaction, structure allowlisting, saved-file verification |
| `cli.py` | `python -m pii_pipeline.cli INPUT --out DIR [--audit]`; exit 0 = released, 1 = blocked, 2 = error |

## Status and limitations

Prototype for a case study. **DOCX and PDF (native text and scanned pages via OCR) are
implemented; PPTX is not implemented yet.** Nothing here is production-ready, and detection is not
complete.

**Threat model.** Aimed at accidental PII in ordinary Office and PDF documents. Deliberately
obfuscated PII (e.g. `jane (at) example dot com`, zero-width or look-alike characters) and
hand-crafted, schema-invalid files are out of scope.

**Detected (pattern-based only, no NER):** email, phone (NANP and `+` international), payment card
(Luhn-checked; card-shaped without Luhn goes to review), US SSN, IPv4/IPv6. Unclaimed runs of 7+
digits go to human review. **Not detected:** person names, postal addresses, dates of birth,
Aadhaar, PAN, IBAN.

Every format: a review finding or residual PII after redaction blocks release, and no redacted
file is kept. Detection precision/recall is unmeasured (`evaluate.py` is not implemented, no
labelled dataset yet). Tests use generated synthetic fixtures only; no real-world documents.

### DOCX

**Fails closed** when the DOCX contains: images, drawings or text boxes (DOCX images are not
OCR'd); embedded/binary parts; a non-standard part with more than one text/attribute field; text
nodes with nested elements; or a DOCTYPE. Legacy `.doc` and encrypted files are rejected. Most real
documents contain images, so most DOCX files will be blocked.

**Metadata changes on release:** core properties reduced to created/modified dates; app properties
reduced to numeric statistics and flags; custom properties, `word/people.xml` and the thumbnail
removed; document variables removed; tracked-change/comment authors replaced with `Author`.

**Known gaps (crafted, schema-invalid XML; not fixed):**
1. A value split across two attributes of an element in a standard Word part
   (e.g. `<w:p w:foo="jane.te" w:bar="ster@example.com">`) can pass with a fragment left behind.
2. A custom part that declares the Office bibliography root namespace is treated as a known part,
   so the same attribute split can pass there.

**Untested:** opening outputs in Microsoft Word or LibreOffice (only python-docx reopening and ZIP
integrity are verified); real Word-authored documents.

### PDF

**How it works.** Native text via PyMuPDF (`page:N`, plus `page:N/link:K` for link URIs); text
split across spans is joined, and a line break after `- @ . / _` joins without a gap. Pages with an
image, or with 10 or more vector path segments, are also OCR'd as `page:N/ocr` with the real mean
Tesseract word confidence; outlined (vector) text is only caught this way, so ordinary pages with
tables or charts need Tesseract. Native units are always kept, so hidden OCR text layers are
redacted too. Redaction removes text under finding boxes, blanks image pixels (white fill on OCR
regions, because black bars degrade re-OCR) and removes vector art touched by OCR boxes. The catalog,
page and link dictionaries are reduced to an allowlist: the tag/structure tree (accessibility
tagging is lost), page thumbnails, XMP and piece info, named destinations and link keys other than
Rect/URI/GoTo are removed; Launch/JavaScript links are dropped. The file is fully rewritten (never
incrementally) and the saved bytes are re-opened, checked, re-extracted and re-OCR'd before the
gate decides.

**Fails closed** when the PDF is encrypted or unparseable; has annotations or form fields;
JavaScript/actions/XFA; optional-content layers; embedded files; outline/bookmarks; text outside the
page area; tagged marked-content text (`/ActualText`, `/Alt`) inside content streams; or any OCR page
that is UNREADABLE (engine error) or PARTIAL (no words, or mean confidence below 0.60). Tesseract
missing when OCR is needed: exit 2 with install instructions; native-only PDFs still work.

**Known gaps (not fixed):**
1. A word broken mid-token across a line (e.g. `jane.tes` / `ter@example.com`) is not rejoined and
   can leave a fragment (documented by a strict-xfail test).
2. Crafted input: fonts, images, ExtGState and other non-page dictionaries are checked against a
   deny-list only, so a custom string key there (e.g. `/Comment` on a font) survives release.

**Limitations:** OCR quality is not guaranteed; low-quality, rotated, handwritten or non-English
scans may be blocked (PARTIAL) or missed, and only English (`eng`) data is installed. A scanned page
whose only text is redacted re-OCRs as empty and is blocked. Text inside images beyond what
Tesseract reads is not detected. OCR redaction blanks whole words and may clip adjacent characters;
it leaves a white gap, not a black bar. Links that use named destinations break.

**Untested:** opening outputs in Acrobat or other viewers; real-world PDFs; rotated pages;
right-to-left scripts and ligature oddities; image soft masks and inline images under redaction
boxes; per-image XMP metadata.

## Setup (Windows)

```powershell
py -3.12 -m venv .venv          # 3.11+ works; 3.12 is what was verified
.venv\Scripts\python -m pip install -r requirements.txt
```

### Tesseract OCR (needed for PDF pages with images or vector drawings)

1. `winget install --id UB-Mannheim.TesseractOCR` (or download the installer from
   https://github.com/UB-Mannheim/tesseract/wiki).
2. Either add `C:\Program Files\Tesseract-OCR` to your user `PATH` and open a new terminal, or set
   it for the session: `$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"`.
3. Check: `tesseract --version`.

## Test

```powershell
.venv\Scripts\python -m pytest -q -rsx
```

Verified on Windows: 100 passed, 1 xfailed with Tesseract available; 99 passed, 1 skipped, 1 xfailed
without it (skip = real-OCR test, xfail = the known mid-token line-break gap).

## Data handling rules

- Never commit real documents. `data/`, `output/`, `audit/` and `*.audit.json` are gitignored.
- Test fixtures must be synthetic and generated in tests (e.g. into `tmp_path`).
- Do not log `TextUnit.text` or PII values; log `doc_id`, `unit_id`, spans and types only.

## File ownership

- Orchestrator: `models.py`, `gate.py`, `requirements.txt`, `.gitignore`, `README.md`,
  `tests/test_gate.py`, `tests/test_models.py`. Changes to contracts go through review.
- Builder: `ingest.py`, `extract.py`, `detect.py`, `redact.py`, `report.py`, `evaluate.py`,
  `package.py`, `pdf_extract.py`, `pdf_redact.py`, `cli.py`, and their tests.
