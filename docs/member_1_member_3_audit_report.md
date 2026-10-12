# Member 1 and Member 3 audit report

Audit date: 2026-10-12. Repository state audited: `master` at `089e879` (M3.11), working tree clean
before the audit. Source of truth: *Optiv Case Study 2 — Revised Technical Solution and
Five-Member Implementation Guide* (55 pages, including the orange "CHANGE" boxes). The original
case-study brief is not in the repository, so wherever this report says "the brief" it means the
brief as the guide quotes it.

Status labels: **PASS** (verified by a test or command run during this audit), **PARTIAL**
(implemented but incomplete or not fully verified), **FAIL** (wrong, missing or incompatible),
**BLOCKED** (cannot be verified without another member's input).

---

## 1. Executive summary

- **The biggest defect was in integration, not in either member's code on its own.** M3's
  detection engine was never called by the pipeline. `pii_pipeline/cli.py` ran M1's regex
  detector only, and M3's `detect_and_resolve` / `detect_strict` were imported and never used.
  The pipeline therefore never detected names, addresses, dates of birth, employee IDs, PAN or
  PESEL, and it sent guide-valid SSNs in area 900 (H5) to review. **Fixed:** the pipeline now uses
  M3's engine, keeps M1's detector as a fail-closed safety net, and the gate's residual scan uses
  `detect_strict` as the guide specifies.
- **M3 had five defects, all now fixed and covered by tests:**
  1. Names with initials were rejected, including the guide's own H6 examples.
  2. Findings carried raw employee IDs in `entity_key`.
  3. `detect_strict` re-parsed the YAML config on every call. This took 202 s for one small DOCX.
  4. IPv4 addresses, valid ISBNs and `v`-prefixed version strings were classed as phone numbers.
  5. The name registry masked bare common words (for example "Title") as surnames.
- **M1's work is a working, well-tested vertical slice, but on a different contract from the
  guide.** M1 used plain dataclasses with `unit_id` strings instead of the guide's pydantic
  `Block`/`Document`/`Finding` schema. There is no YAML pipeline config, run manifest, logging
  filter, `--inject-miss` option or folder CLI. M1 also built extraction (M2's area), redaction
  (M4's) and evaluation tooling (M5's). That overlap is the most important coordination issue for
  the three pending members.
- **Measured effect (synthetic benchmark, 45 files, seed 1).**
  - Recall rose from 0.63 to 0.95.
  - Released files still holding any labelled value fell from 47 to 0.
  - Precision fell from 0.95 to 0.08. The cause is M3's offline name heuristic: spaCy is not
    installed, so every pair of capitalised words, including Word style names, is masked as a
    person. This fails safe (it over-masks), but it misses the guide's 90 % precision target by a
    wide margin and needs a team decision (Section 9, D3).
- **Final state:** 182 passed, 1 skipped, 2 expected failures (one existing, one new that records
  the precision problem). End-to-end readiness on the real samples is **not** established: the
  three sample files and the truth set are not in the repository.

## 2. Requirements and expected outcomes

From the guide (Sections 1–3, 13 and 14):

| ID | Requirement |
|---|---|
| R1 | Route each file by its real type; extract PDF (OCR at 300 dpi for scanned pages), DOCX and PPTX into one shared record format |
| R2 | Detect and classify PII: employee IDs, names (full and partial), email, phone, government IDs (SSN with area 900 allowed, PAN, PESEL including masked, passport), DOB, home address, last-four fragments, family-member names |
| R3 | Allow-list organisation-level look-alikes with reason codes; never let the allow-list silently override strong PII evidence |
| R4 | Replace each finding with a stable typed tag such as `[PERSON_003]`; deterministic per document; never link people across documents |
| R5 | Release gate: PASS only if extraction is complete, nothing is unreadable, nothing high-risk is unresolved and the stricter residual scan is clean |
| R6 | Findings report traceable to page/slide/cell; reason code on every suppression; no raw values in logs or reports |
| R7 | At least 80 % structure retained (SRS) |
| R8 | Local only, no network; pinned dependencies; repeatable output |
| R9 | Metrics: strict recall (99 % native, 97 % OCR, 100 % government IDs), precision 90 %, gate effectiveness, traceability, safe-release rate |
| R10 | Vertical slice first; contracts and fixtures published first (M1) |

Expected outputs of **M1**: contracts with JSON schemas and fixtures (`contracts-v1`),
stakeholder questions A1/A2/A3/A8 recorded, pipeline + CLI + YAML config, run manifest and
PII-safe logging, release gate with `--inject-miss`, continuous integration of modules, frozen
build, and the deck.

Expected outputs of **M3**: the `detect/` package (L1 patterns and validators, L2 context and
table typing, L3 spaCy PERSON, L4 name registry, L5 resolver), `config/allowlist.yaml`,
`detect_strict` for the gate, findings for all samples, frozen thresholds, and one slide.

## 3. Member 1 audit

| # | Guide expectation | Implementation | Evidence | Status |
|---|---|---|---|---|
| M1-1 | Pydantic contracts `Locator`/`Block`/`Page`/`Document`/`Finding`/`GateVerdict`, JSON schemas, `ir_min.json` and `findings_min.json` fixtures, tag `contracts-v1` | `pii_pipeline/models.py`: dataclasses `SourceDocument`, `TextUnit`, `ExtractedDocument`, `Finding`, `RedactionResult`, `GateDecision`. Locators are `unit_id` strings (`para:3`, `table:0/r1/c2/para:0`, `page:3/ocr`, `pkg:<part>#n`). No pydantic, schemas, fixtures or tag. | `tests/test_models.py`; file inspection | **PARTIAL.** It works, but it is not the guide's contract: no `bbox`, `dpi`, `offset_map`, `kind`, `reading_order`, `Page.route` or `unread_images`. Decision D1. |
| M1-2 | Send A1, A2, A3, A8 and record answers or defaults in the README | README has a threat model but no assumptions list | README inspection | **FAIL** (no evidence). Also cannot verify whether anything was sent. |
| M1-3 | `pipeline.run(paths, cfg)`, `config/default.yaml`, `python -m pipeline run samples/ --out out/` | `pii_pipeline/cli.py`: one file per call, exit codes 0/1/2. Thresholds are in code; `config/default.yaml` does not exist (M3's loader looks for it and skips it). | Tests pass; file inspection | **PARTIAL** (no folder run, no YAML pipeline config). |
| M1-4 | `manifest.json` per run (SHA-256, config hash, library versions); logging filter; error-code-only exceptions | Exceptions print the class name only (`cli.py:62-67`). No manifest and no logging configuration. | File inspection | **FAIL** (manifest and logging filter missing). Exception hygiene is PASS. |
| M1-5 | Gate: strict residual scan with tags removed, LOW_OCR, UNREAD_IMAGE, `--inject-miss` | `gate.decide`: incomplete extraction, unreadable or partial units (OCR mean confidence below 0.60 becomes PARTIAL), REVIEW findings, and residual findings from a full re-extraction of the saved output file. Images in DOCX/PPTX make extraction incomplete, so they block. No `--inject-miss`. **The residual scan used the normal detector, not `detect_strict`.** | `tests/test_gate.py` (8 cases); `tests/unit/test_slice_m3.py` | **PARTIAL → improved.** The residual scan now uses `detect_strict`. Re-scanning the saved file is stronger than the guide asks. `--inject-miss` is still missing. |
| M1-6 | Swap the stubs for real modules: extractor, detector (M3), redactor, gate | `pii_pipeline/detect.py` re-exported M3's functions at the bottom of the file, but `cli.run` called M1's own `detect.detect` | Probe run: the CLI used only `regex:email` and `regex:unclaimed_digit_run`; names and DOB never detected | **FAIL → fixed** (Section 7, C1) |
| M1-7 | Freeze; two runs give identical redacted-text hashes (E-02) | Not done before the audit | Run during this audit on the four demo inputs: redacted-text hashes equal, findings equal; **file bytes differ** (ZIP/PDF timestamps) | **PASS** for redacted text and findings. The guide's E-02 wording ("redacted-text hashes") is met. Byte-identical files are not. |
| M1-8 | Deck and submission pack | Not in the repository | — | **BLOCKED** (not started or not shared) |
| M1-9 | Pinned requirements | `requirements.txt` was missing `phonenumbers`, `python-stdnum` and `PyYAML`, which the detect engine uses. Without them it silently falls back to weaker code paths. | `pip list` in `.venv` | **FAIL → fixed** (C9) |
| M1-10 | (Not M1's in the guide) extraction, OCR, redaction, report, evaluation, benchmark, demo | All present and tested: `ingest.py`, `extract.py`, `package.py`, `pdf_extract.py`, `pdf_redact.py`, `redact.py`, `report.py`, `evaluate.py`, `benchmark.py`, `demo.py` | 130+ tests | **Scope overlap** with M2, M4 and M5. Decision D2. |

Further M1 observations:

- M1's own SSN rule rejects area 9xx, which contradicts H5. It no longer decides anything,
  because it now runs only as the safety net under M3. Where M3 misses such a number, the net
  still sends the digits to REVIEW, so the result fails closed.
- The redactor writes untyped placeholders: `[PERSON]`, `[OTHER]` (an employee ID becomes
  `[OTHER]`), `[EMAIL]`. These are not R4's stable `[PERSON_001]` tags. M3 already computes those
  tags (`entity_type_tag`). Using them is M4's token-factory work (Q4.1).
- The report dropped every detection-engine field, including reason codes. **Fixed** (C8).
- The benchmark's decoy classifier counted allow-listed (KEEP) records as "review". **Fixed**
  (C10).

## 4. Member 3 audit

| # | Guide expectation | Implementation | Evidence | Status |
|---|---|---|---|---|
| M3-1 | Email by syntax, OCR-damaged domains (U-06) | `recognizers/email.py` | `tests/unit/test_email.py` | **PASS** |
| M3-2 | Phone across 8 regions + lenient ≥8-digit fallback, including `+1 (555) 0114` (U-07, H7) | `recognizers/phone.py` (phonenumbers + NANP + international + regional + cued bare digits) | `test_phone.py`; probe | **PASS** after fixing look-alikes: IPv4, valid ISBN-13 and `v`-prefixed versions were tagged as phones (C5) |
| M3-3 | SSN with area 900–999 allowed (U-08, H5); PAN; PESEL checksum + masked (U-09) | `recognizers/govid.py` | `test_govid.py`; probe | **PASS** |
| M3-4 | Passport and TAX_ID | Not implemented. A TODO says formats are unconfirmed. `_PASSPORT_CTX_RE` is defined but never used. The test is skipped. | `test_govid.py:63` skipped | **BLOCKED** on sample formats (Q5.2). Mitigation: digit-only passports of 7+ digits now hit the safety net's REVIEW rule, so they block rather than pass. Alphanumeric passports are not covered. |
| M3-5 | Employee/director/vendor IDs from config (U-10, A4) | `recognizers/ids.py` + `id_formats` in `allowlist.yaml` | `test_ids.py`; probe | **PASS** |
| M3-6 | DOB only after a cue within 25 chars; last-four fragments; home address; family cue (U-11, U-12) | `recognizers/dob.py`, `fragments.py`, `address.py`, `context.py` | `test_context.py`; probe | **PASS** for DOB, fragments and family. Address is **PARTIAL**: US-style street patterns only. |
| M3-7 | Allow-list with reason codes; suppress only with supporting context; conflicts go to REVIEW | `resolver.py`: validated HIGH-severity types conflicting with the allow-list become REVIEW with reason `ALLOWLIST_CONFLICT`; every allow record carries a reason code | `test_resolver.py`; probe | **PASS** for the mechanism. Company, HQ and hotline values are **BLOCKED** on the samples (Q5.2). |
| M3-8 | Resolver uses explicit priorities, not raw score comparison | `resolver.py` priority tiers | `test_resolver.py` | **PASS** |
| M3-9 | L3: spaCy PERSON on each block and on a title-cased copy | `ner.py`: spaCy if installed, **plus** an always-on capitalisation heuristic. spaCy is not installed and not in the requirements. | Probe: "New York", "Please Note", "Data Protection" and Word style names masked as people; benchmark: 4,761 person FPs | **FAIL (precision).** Initials bug fixed (C2). Over-masking recorded as a strict-xfail test. Decision D3. |
| M3-10 | L4: per-document registry; no cross-document linking; conservative surname matching | `registry.py` | `test_ner_and_registry.py` | **PASS** after fixing two bugs: a variant such as "E. Rostova" was registered as a new person, making the surname look non-unique (C3); bare surnames of heuristic-only "people" were matched without corroboration, so "Title" from the style "Book Title" was masked everywhere (C6) |
| M3-11 | Table column typing; header cells scanned (H11) | `context.py` (parses `table:N/rR/cC` and `slide:N/shape:K/table/rR/cC` locators) | `test_context.py` | **PASS.** The engine ignored the caller's config for header cells; fixed (C7). |
| M3-12 | `detect_strict(text, cfg)` for the gate, threshold 0.30, no allow-list for HIGH types | `engine.py` | `test_ner_and_registry.py`; `test_slice_m3.py` | **PASS** functionally. It re-parsed YAML on every call when `cfg` was None (202 s for one DOCX); fixed (C4). Design conflict D4 below. |
| M3-13 | Findings never hold raw values | `models.py` says so, but `entity_key` held raw employee IDs (`EMP-40000`), so `model_dump_json()` leaked them. Names were stored as an unsalted SHA-256, which can be reversed by enumeration and would link the same name across documents (H12). | Probe | **FAIL → fixed** (C3) |
| M3-14 | Thresholds tuned on development pages and frozen (Step 9) | Default thresholds from the guide | — | **BLOCKED** on M5's truth set and dev/held-out split |
| M3-15 | Findings for all three samples; one slide | Samples not in the repository | — | **BLOCKED** |

**Design conflict D4.** Strict mode never applies the allow-list to HIGH types, and the guide
classes the HQ address as HIGH and allow-listed (A3). After the first pass keeps the HQ address,
the residual scan will flag it and **block every document that mentions the HQ address**. This
does not happen today only because `hq_addresses` is empty. Resolve it before filling that list.

## 5. Combined integration audit (M1 + M3)

Data flow after the fixes (`pii_pipeline/cli.py`):

```
ingest.load -> extract.extract -> detect.detect -> redact.redact (draft file)
            -> extract.extract(draft) -> detect.detect_residual -> gate.decide -> report.write_report
```

- **Interface fit.** M3's `DetectedFinding` subclasses M1's `Finding`, so the redactor, gate,
  report and evaluator accept it unchanged. M3's engine reads M1's `ExtractedDocument` directly and
  derives table coordinates from `unit_id`.
  - Verified for DOCX, PPTX, native PDF and scanned PDF, by the full test suite and the demo.
- **`detect.detect`** returns M3's findings, including allow records as `Action.KEEP` with an
  `ALLOW_*` reason, followed by safety-net findings from M1's original regex detector. A net
  finding is kept only when M3 has not already decided that span (masked it, sent it to review,
  or allow-listed it). The net:
  - keeps the fail-closed rule that unclaimed 7+ digit runs go to REVIEW;
  - keeps IP-address detection, which M3 does not have;
  - cannot override M3's allow-list (H9 `INC-…` IDs, A3 generic mailboxes).
- **`detect.detect_residual`** applies M3's `detect_strict` to each unit of the re-extracted
  output (placeholder tags ignored), plus the same net.
- **Shared assumptions now consistent:**
  - Offsets index `TextUnit.text`.
  - `Action.KEEP` means "allowed, with a reason".
  - REVIEW blocks release.
  - Findings hold no values.
  - `entity_key` is an opaque per-document key.
- **Remaining gaps:**
  - Placeholders are untyped (M4).
  - OCR confidence below 0.70 does not trigger M3's low-confidence masking. M1's PDF extractor
    marks pages below 0.60 PARTIAL, which blocks release, and passes M3 a page-level mean only,
    not per-span confidence. The guide's 0.50–0.79 "mask, fail closed" band per span is
    therefore not implemented (needs per-word confidence from M2).
  - Without spaCy, name precision is very low (D3).
- **Performance.** Detection takes about 1–3 s per DOCX. M1's package scanner turns every XML
  text node into a unit, about 8,600 units even for a near-empty document, mostly style names.
  The suite went from 143 s to about 300–420 s.

## 6. Requirement traceability matrix

| Requirement / expected outcome | Member | Implementation | Verification evidence | Status | Required action |
|---|---|---|---|---|---|
| R1 content-based routing | M2 (built by M1) | `ingest.load` (magic bytes, ZIP sniff, extension must match) | `tests/test_docx_*`, `test_pdf*` | PASS (fixture files) | M2: test against the three real samples (U-01) |
| R1 OCR for scanned PDF | M2 (built by M1) | `pdf_extract.py` (Tesseract, per-page OCR, PARTIAL below 0.60) | `test_pdf.py` real-OCR tests, demo case 4 | PARTIAL | 300 dpi, figure crops, OCR of DOCX/PPTX images (H2) are open; images block release instead |
| R1 shared record format | M1 | `models.py` dataclasses | `test_models.py` | PARTIAL | D1 |
| R2 structured PII (email, phone, SSN 9xx, PAN, PESEL, employee IDs) | M3 | `detect/recognizers/*` | unit tests; `test_m1_m3_integration.py` | PASS | — |
| R2 names (H3, H6) | M3 | `ner.py`, `registry.py` | unit tests; benchmark person recall 1.00 | PARTIAL (precision) | D3 |
| R2 DOB, last-four, address, family | M3 | `dob.py`, `fragments.py`, `address.py`, `context.py` | unit tests; benchmark | PASS / address PARTIAL | Widen address patterns after samples |
| R2 passport, TAX_ID | M3 | not implemented | skipped test | BLOCKED | Q5.2 |
| R3 allow-list with reasons, precedence | M3 | `resolver.py`, `allowlist.yaml` | `test_resolver.py`; integration test (`ALLOW_REF_ID_INC` in the report) | PASS (mechanism) | Values from the samples; D4 |
| R4 stable typed tags | M4 (+M3 keys) | M3 computes `entity_type_tag`/`entity_key`; the redactor writes `[TYPE]` | probe output | FAIL (not used) | Q4.1 |
| R5 gate | M1 | `gate.decide` + `detect_residual` | `test_gate.py`; `test_m1_m3_integration.py` | PARTIAL | `--inject-miss`; I-04 against real samples |
| R6 traceable report, reasons, no values | M4 (built by M1) | `report.py` | `test_m1_m3_integration.py`, demo tests (values absent) | PARTIAL | Per-page/type summary and CSV (M4); a manifest (M1) |
| R7 SRS ≥ 0.80 | M2, M5 | — | — | BLOCKED | M5 |
| R8 local only, pinned, repeatable | M1 | no network code found; requirements pinned (C9); E-02 run | E-02 run in this audit | PARTIAL | S-01 no-network test (M5); manifest (M1) |
| R9 metrics | M5 | `evaluate.py`, `benchmark.py` (synthetic only) | benchmark run | BLOCKED for real samples | M5 truth set |
| R10 vertical slice | M1, M3 | DOCX/PDF/PPTX slices; `test_slice_m3.py` | tests | PASS | — |

## 7. Corrections applied

| ID | File(s) | Change | Why |
|---|---|---|---|
| C1 | `pii_pipeline/detect.py`, `pii_pipeline/cli.py` | `detect()` now runs M3's engine plus the original regex detector as a safety net on spans the engine has not decided. New `detect_residual()` uses `detect_strict` per unit. The original detector is kept as `pattern_detect()`. | The guide's integration step: the pipeline must call M3's detector and the gate must use `detect_strict`. Before this, names, DOB, IDs, PAN and PESEL were never detected in the pipeline. |
| C2 | `detect/ner.py` | Keep the period on initials when validating a name span; reject spans made only of initials | "Rafael E. Duarte" and "S. Marchetti-Lang" (guide H6) were rejected outright, which could leave a partial name such as "Rafael E." unmasked |
| C3 | `detect/resolver.py`, `detect/registry.py` | `entity_key` becomes an opaque per-document ordinal (`ek_0001`). The registry merges a name that is a variant of exactly one known person into that person and links an ambiguous variant to nobody. | Raw employee IDs leaked through `model_dump_json()` (M1's contract rule, revised guide "mapping file security"); a value hash is reversible and links across documents (H12). The registry fix stops a correct new detection ("E. Rostova") from making the surname look non-unique. |
| C4 | `detect/config.py`, `pii_pipeline/detect.py` | Cache parsed YAML by path and modification time and return a deep copy; load the config once per residual scan | `detect_strict` re-parsed YAML on every call: 202 s for one demo DOCX, now 0.7 s. The deep copy keeps `load_config(dict)`, which mutates its result, from changing the cache. |
| C5 | `detect/recognizers/phone.py` | Reject dotted-quad IPv4, valid ISBN-13 (no `+`) and dotted numbers right after `v`/`V` | Look-alikes (H9) were tagged as phones. The `v` rule is deliberately narrow, so OCR-glued numbers such as `Call5551234567` are still phones (tested). |
| C6 | `detect/registry.py` | A bare surname matches only for an ID-seeded person or with a nearby cue | Revised guide: "require context for weak matches". "Title" was masked across whole documents. |
| C7 | `detect/engine.py` | Pass the resolved config into table typing | Header-cell name detection ignored a caller's config (for example a custom company allow-list) |
| C8 | `pii_pipeline/report.py` | Report the detection-engine fields when present: `entity_type`, `category`, `severity`, `reason`, `validator`, `entity_type_tag`, `entity_key`, `detectors` | The report dropped reason codes, so suppressions were invisible to audit (R3, R6). No field holds a value. |
| C9 | `requirements.txt` | Pin `phonenumbers==9.0.41`, `python-stdnum==2.2`, `PyYAML==6.0.3` | Without them a fresh checkout silently degrades detection (R8) |
| C10 | `pii_pipeline/benchmark.py` | Decoy outcome ignores KEEP (allow-listed) records | Allow-listed amounts were counted as "review" |
| C11 | `config/allowlist.yaml` | `company_names: [Microsoft]` with a comment | Office writes "Microsoft … Word" into `docProps/app.xml` of every document and the name heuristic masked it. This is not a sample value. |
| T | `tests/…` | New `tests/test_m1_m3_integration.py` (CLI end-to-end with the guide's hard cases; safety-net and allow-list interplay; residual catch; strict-xfail for name over-masking). Regression tests for initials, ambiguous variants and phone look-alikes. Updated assertions that encoded the old behaviour: raw `entity_key`, "no person is ever detected", "every finding is REVIEW" (now scoped to actionable or body findings, because `detect()` now returns allow records as the guide's interface requires). | Each updated assertion encoded a defect or the pre-integration behaviour; the reason is in the test comment |

Nothing in the M2, M4 or M5 areas of the guide was rewritten. The changes to `report.py` and
`benchmark.py` are small compatibility fixes inside files M1 wrote.

## 8. Tests and verification results

| Check | Command | Result |
|---|---|---|
| Baseline suite before changes | `.venv\Scripts\python -m pytest -q -rsx` | Passed: 176 passed, 1 skipped, 1 xfailed (143 s) |
| Final suite | same | Passed: **182 passed, 1 skipped, 2 xfailed** (425 s, Tesseract available) |
| M3 unit tests | `pytest tests/unit` | Passed: 45 passed, 1 skipped |
| Integration probe (guide hard cases through the real CLI) | scratch script | Before: gate BLOCKED, detectors `regex:email` + `regex:unclaimed_digit_run` only. After: released; `Jane Doe`, `EMP-40001`, `14 July 1990`, `900-55-2043`, `+1 (555) 0114` and `2218` absent from the output |
| E-02 repeatability (demo inputs 1–4, two runs) | scratch script | Passed for redacted text and findings; output file bytes differ (timestamps) |
| Synthetic benchmark | `python -m pii_pipeline.benchmark --out tmp/benchmark2 --seed 1` | See table below |
| Lint / type check | — | Not run: no linter or type checker is configured in the repository |
| Real samples (Policy PDF, OrgPack PPTX, TPRM DOCX) | — | **Blocked**: the files are not in the repository |
| S-01 to S-04, I-01 to I-04 against samples | — | **Blocked** (M5; samples) |

Synthetic benchmark, seed 1, 45 files. "Before" is from the README; "after" is from this audit's
run on the final code:

| Type | Gold | Before TP / FP / FN | After TP / FP / FN | Before prec / rec | After prec / rec |
|---|---|---|---|---|---|
| email | 57 | 56 / 1 / 1 | 56 / 1 / 1 | 0.98 / 0.98 | 0.98 / 0.98 |
| phone | 53 | 37 / 1 / 16 | 41 / 10 / 12 | 0.97 / 0.70 | 0.80 / 0.77 |
| credit_card | 54 | 54 / 0 / 0 | 53 / 0 / 1 | 1.00 / 1.00 | 1.00 / 0.98 |
| us_ssn | 56 | 56 / 0 / 0 | 52 / 4 / 4 | 1.00 / 1.00 | 0.93 / 0.93 |
| ip_address | 57 | 56 / 11 / 1 | 55 / 11 / 2 | 0.84 / 0.98 | 0.83 / 0.96 |
| person | 44 | 0 / 0 / 44 | 44 / 4,761 / 0 | – / 0.00 | 0.01 / 1.00 |
| address | 45 | 0 / 0 / 45 | 45 / 0 / 0 | – / 0.00 | 1.00 / 1.00 |
| date_of_birth | 45 | 0 / 0 / 45 | 43 / 0 / 2 | – / 0.00 | 1.00 / 0.96 |
| **overall** | 411 | 259 / 13 / 152 | 389 / 4,787 / 22 | 0.95 / 0.63 | **0.08 / 0.95** |

Pipeline outcomes:

- **Released:** 18 of 45 (16 before).
- **Released files still holding a labelled value:** 0 (47 before, all names, addresses or DOB).
- **Decoys (after):** amounts 25/25 clean and versions 25/25 clean. Order numbers, invalid cards
  and invalid IPs go to REVIEW, as before.

A first after-run made before fixes C5 and C10 gave phone FP 36, amounts 25 in review and versions
25 masked. That run is how those two defects were found.

The `us_ssn` drop (52 instead of 56 TP) comes from overlap resolution with other detectors in a few
mixed and OCR files. It was not investigated further. It is listed for M3 to examine with M5.

How to read it:

- **Person, address and DOB went from undetected to detected.**
- **Precision collapsed** because the name heuristic masks package text such as style names.
- **The `us_ssn` false positives are the benchmark's area-9xx "invalid SSN" decoys.** The guide's
  H5 says these must be masked, so the benchmark's labels contradict the guide.
- **ISBN decoys are still masked:** they are ISBN-shaped but have invalid check digits.
- **This is a synthetic corpus written by the same team.** It is not evidence for the real
  samples.

## 9. Pending work from Members 2, 4 and 5

Much of the guide's M2, M4 and M5 scope already exists, built by M1. Each pending member should
**start from the existing code**, not the guide's empty layout.

**Member 2 — ingestion, extraction, OCR.** Existing: `ingest.py`, `extract.py`, `package.py`,
`pdf_extract.py`. Missing against the guide:

- OCR of DOCX/PPTX images and PDF figure crops (H2); today any image blocks release.
- Rasterising at 300 dpi (check `pdf_extract.py`).
- Per-word OCR confidence and bounding boxes.
- PDF page route NATIVE/SCANNED/MIXED reported per page.
- An extraction report.
- Runs on the three samples (U-01 to U-04).

**Member 4 — redaction, reporting, demo.** Existing: `redact.py`, `pdf_redact.py`, `report.py`,
`demo.py`. Missing:

- Stable typed tags (R4).
- Pipe-separated table rows and page/slide markers in an LLM text package.
- Summary by file, type, severity and page, plus CSV.
- An audit-mapping file outside the LLM package with restricted permissions.
- `demo.ipynb` and the backup recording.

**Member 5 — truth, evaluation, tests.** Existing: `evaluate.py` and `benchmark.py` (synthetic,
type-level scoring). Missing:

- Truth CSVs for the samples and the frozen dev/held-out split.
- Strict recall with the leak check.
- SRS (S1/S2/S3).
- Tests S-01 to S-04 and I-01 to I-04.
- Safe-release rate.
- `metrics.json`.

## 10. Dependency and blocker matrix

| Pending member | Required deliverable / decision | Why it is needed | Affected member / component | Exact question or action required | Blocking status |
|---|---|---|---|---|---|
| Team (M1 decides) | **D1** contract: keep M1's `TextUnit`/`unit_id` dataclasses or move to the guide's pydantic `Block`/`Locator` | M2, M4 and M5 must build against one contract; M3 already reads both | M1 `models.py`; all modules | "Do we freeze `pii_pipeline/models.py` as contracts-v1 and add only `bbox`/`page`/`ocr_conf` fields as needed, or adopt the guide's schema?" | **Hard blocker** for new M2/M4/M5 code |
| Team (M1 decides) | **D2** scope split: M1 already built M2's, M4's and M5's core modules | Avoid duplicate rewrites | M2, M4, M5 | "Are M2/M4/M5 extending `extract.py`/`pdf_extract.py`, `redact.py`/`report.py` and `evaluate.py` respectively?" | **Hard blocker** (rework risk) |
| Team + stakeholder (A1) | **D3** name detection: install spaCy `en_core_web_md` (guide Section 5) and restrict the capitalisation heuristic to corroborated contexts | Precision 0.08 on the benchmark; style names masked | M3 `ner.py`; M5 metrics; M1 `requirements.txt` | "Is local spaCy approved (A1)? If yes, M1 pins it and M3 makes the heuristic need a cue (table column, ID in row, honorific)." | **Hard blocker** for precision tuning; soft for everything else (fails safe) |
| M3 + M1 | **D4** strict mode vs allow-listed HQ address (HIGH) | The gate would block every document that mentions the HQ address | M3 `resolver.py`; M1 gate | "Should strict mode honour `hq_address` allow-list entries (exact match only)?" | Soft: no effect until `hq_addresses` is filled |
| M5 | Truth CSVs + dev/held-out split | M3 Step 9 tuning and freeze; all R9 numbers | M3 thresholds; M1 deck | "Which pages are development pages?" | **Hard blocker** for M3 freeze |
| M5 / whoever holds them | The three sample files in the repository (or a shared local path, gitignored) | Hard-case tests, allow-list values, passport/TAX_ID formats | M3 allow-list, govid; M1 E-01 | "Where are the Policy PDF, OrgPack PPTX and TPRM DOCX?" | **Hard blocker** for sample verification |
| M5 | Passport and TAX_ID formats seen in the samples (Q5.2) | M3-4 is unimplemented | M3 `govid.py` | "List each passport/tax-ID format with page reference." | Hard blocker for M3-4 |
| M4 | **Q4.1** use M3's `entity_type_tag` in the redactor; tag format `[TYPE_NNN]` per document | R4 | M4 redactor; M1 residual scan (tag regex already ignores `[A-Z_0-9]` tags) | "Confirm `entity_key` is treated as an opaque per-document key, scoped by `doc_id`." | Soft (pipeline works with untyped tags) |
| M2 | Per-word OCR confidence and boxes; image OCR (H2) | The guide's per-span low-OCR mask band; images currently block | M3 resolver (`ocr_confidence`); M1 gate | "Will `TextUnit` carry per-span confidence, or only the page mean?" | Soft |
| M1 | Manifest, logging filter, `--inject-miss`, folder CLI, YAML pipeline config, assumptions in the README | E-01, S-02, I-04, R8 | M1 | Action, no question | Non-blocking for M3 |

## 11. Next actions for Member 1

Can proceed now (no input needed):

- Add the run manifest (SHA-256, config hash, versions via `importlib.metadata`).
- Add the PII-safe logging filter.
- Add `--inject-miss` and the I-04 gate tests (injected email, low-OCR unit, unread image, clean
  control).
- Add a folder mode to the CLI.
- Record A1/A2/A3/A8 defaults in the README.
- Decide D1 and D2 and tell the team. These are the first things M2, M4 and M5 need.
- Update the README's detection section and benchmark table. It still says names are never
  detected. *(This audit did not edit the README, because it is M1's document.)*

Must wait:

- Freezing and tagging the release (M5 metrics).
- The deck numbers (M5's `metrics.json`).

## 12. Next actions for Member 3

Can proceed now:

- Make the name heuristic need corroboration when spaCy is unavailable, or gate it on spaCy.
  This needs D3 first if spaCy is to be added; designing the rule can start now.
- Resolve D4 with M1.
- Use the unused `_PASSPORT_CTX_RE` once the formats are known.
- Consider skipping name detection for units from purely structural package parts (styles,
  theme, font table, numbering). This needs M1's agreement, because it couples to M1's `unit_id`
  scheme.

Must wait:

- Allow-list values (company names, HQ address, hotlines), passport/TAX_ID formats: the samples.
- Threshold tuning and freeze: M5's dev pages.
- Findings for the three samples and the slide: samples and M5.

Must remain unchanged until confirmed:

- The `entity_key` and `entity_type_tag` semantics (M4 Q4.1).
- `detect_strict`'s signature (M1's gate uses it).

## 13. Final readiness assessment

- **Verified:**
  - M3's detectors on synthetic hard cases (H3–H11 equivalents).
  - The M1 pipeline with M3 wired in, end to end, for DOCX, PPTX, native and scanned PDF on
    synthetic inputs.
  - The gate's fail-closed rules.
  - No raw values in findings or reports for the tested cases.
  - Repeatable redacted text and findings.
- **Incomplete:**
  - Contracts versus the guide (D1).
  - Manifest, logging filter, `--inject-miss`, folder CLI.
  - Passport/TAX_ID.
  - Name precision (D3).
  - Typed tags (M4).
  - Image OCR (M2).
  - All sample-based and metric-based acceptance (M5).
- **What prevents completion:**
  - The three sample files and the truth set are absent.
  - D1, D2 and D3 are undecided.
  - M2, M4 and M5 have not started.
- **The team cannot claim** any of the guide's numeric targets yet. The only measured numbers are
  on M1's synthetic benchmark, where recall is 0.95 and precision 0.08.
