"""PDF extraction, OCR routing (fake engine unless Tesseract exists), redaction and fail-closed release.
All fixtures are synthetic and generated in tmp_path."""
import io

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageStat

from pii_pipeline import cli, detect, extract, ingest, pdf_extract, redact
from pii_pipeline.models import ExtractionMethod, PIIType, UnitStatus
from pii_pipeline.pdf_extract import OcrWord

EMAIL, PHONE, SSN = "jane.tester@example.com", "415-555-0132", "123-45-6789"
DPI = 100
PAGE_W, PAGE_H = 827, 1170       # A4 (PyMuPDF default page) rendered at 100 dpi


# ---- fixtures ----------------------------------------------------------------------------------

def native_pdf(path, lines=(), setup=None):
    d = pymupdf.open()
    p = d.new_page()
    y = 72
    for text in lines:
        p.insert_text((72, y), text, fontsize=11)
        y += 20
    if setup:
        setup(d, p)
    d.save(str(path))
    return path


def png_page(specs):
    """White A4 page image at 100 dpi with black text; returns (png bytes, [(text, l, t, w, h)])."""
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    dr = ImageDraw.Draw(img)
    words = []
    for text, left, top in specs:
        dr.text((left, top), text, fill="black")
        l, t, r, b = dr.textbbox((left, top), text)
        words.append((text, l, t, r - l, b - t))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), words


def scanned_pdf(path, specs, native_first=None):
    d = pymupdf.open()
    if native_first:
        d.new_page().insert_text((72, 72), native_first, fontsize=11)
    p = d.new_page()
    png, words = png_page(specs)
    p.insert_image(p.rect, stream=png)
    d.save(str(path))
    return path, words


class FakeOcr:
    """Reports each known word unless its region is uniform (blank/blacked out), like a real engine."""

    def __init__(self, words, conf=92.0):
        self.words, self.conf = words, conf

    def __call__(self, image):
        out = []
        for i, (text, l, t, w, h) in enumerate(self.words):
            region = image.crop((l, t, l + w, t + h)).convert("L")
            if ImageStat.Stat(region).stddev[0] > 20:
                out.append(OcrWord(text, self.conf, l, t, w, h, ("b", 1, i)))
        return out


@pytest.fixture
def fake_ocr(monkeypatch):
    monkeypatch.setattr(pdf_extract, "OCR_DPI", DPI)
    monkeypatch.setattr(pdf_extract, "ocr_available", lambda: True)

    def install(words, conf=92.0):
        monkeypatch.setattr(pdf_extract, "ocr_words", FakeOcr(words, conf))
    return install


def doc_of(path):
    return extract.extract(ingest.load(path))


def pdf_text_and_streams(path) -> str:
    """Everything recoverable from the PDF: page text, hidden text, links, metadata, raw objects/streams."""
    d = pymupdf.open(str(path))
    parts = [str(d.metadata)]
    for p in d:
        parts.append(str(p.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)))
        parts.append(" ".join(str(link) for link in p.get_links()))
    for x in range(1, d.xref_length()):
        try:
            parts.append(d.xref_object(x, compressed=False))
            parts.append(d.xref_stream(x).decode("latin-1", "ignore") if d.xref_is_stream(x) else "")
        except Exception:
            pass
    d.close()
    return "\n".join(parts)


def doc_text(path) -> str:
    return " ".join(u.text for u in doc_of(path).units)


def released(out, ext="pdf"):
    return list(out.glob(f"*.{ext}"))


# ---- native extraction -------------------------------------------------------------------------

def test_native_units_and_locators(tmp_path):
    doc = doc_of(native_pdf(tmp_path / "n.pdf", [f"Mail {EMAIL}"]))
    u = doc.units[0]
    assert u.unit_id == "page:1" and u.method is ExtractionMethod.NATIVE and u.status is UnitStatus.OK
    assert EMAIL in u.text and doc.complete
    f = detect.detect(doc)
    assert [x.pii_type for x in f] == [PIIType.EMAIL] and f[0].unit_id == "page:1"
    assert u.text[f[0].start:f[0].end] == EMAIL


def test_native_pdf_end_to_end_clean_release(tmp_path, capsys):
    def setup(d, p):
        d.set_metadata({"title": "Owner Jane Q Public", "author": "Jane Q Public"})
    src = native_pdf(tmp_path / "n.pdf", [f"Mail {EMAIL}", f"Call {PHONE}", f"SSN {SSN}"], setup)
    out = tmp_path / "out"
    assert cli.main([str(src), "--out", str(out)]) == 0
    rel = released(out)
    assert len(rel) == 1 and rel[0].name.endswith(".redacted.pdf")
    blob = pdf_text_and_streams(rel[0])
    for s in (EMAIL, "jane.tester", PHONE, "0132", SSN, "Jane Q Public"):
        assert s not in blob, s
    assert pymupdf.open(str(rel[0])).page_count == 1
    shown = capsys.readouterr()
    rep = next(out.glob("*.report.json")).read_text()
    assert not any(s in shown.out + shown.err + rep for s in (EMAIL, PHONE, SSN))
    assert not list(out.glob("*UNSAFE-DRAFT*")) and not list(out.glob("*.audit.json"))


def test_pii_split_across_spans_and_wrapped_lines(tmp_path):
    def setup(d, p):
        w = pymupdf.get_text_length("Mail jane.tes", fontsize=11)
        p.insert_text((72, 140), "Mail jane.tes", fontsize=11)
        p.insert_text((72 + w, 140), "ter@example.com", fontsize=11)       # second span, same line
        p.insert_text((72, 170), "Phone 415-555-", fontsize=11)            # wrapped across lines
        p.insert_text((72, 184), "0132", fontsize=11)
    src = native_pdf(tmp_path / "s.pdf", [], setup)
    kinds = sorted(f.pii_type.value for f in detect.detect(doc_of(src)))
    assert kinds == ["email", "phone"]
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    blob = pdf_text_and_streams(released(tmp_path / "o")[0])
    assert "ter@example" not in blob and "jane.tes" not in blob and "0132" not in blob


@pytest.mark.parametrize("kw", [{"render_mode": 3}, {"color": (1, 1, 1)}], ids=["invisible", "white"])
def test_invisible_and_white_text_is_extracted_and_removed(tmp_path, kw):
    src = native_pdf(tmp_path / "h.pdf", [], lambda d, p: p.insert_text((72, 100), f"hidden {EMAIL}", **kw))
    assert any(f.pii_type is PIIType.EMAIL for f in detect.detect(doc_of(src)))
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    assert "jane.tester" not in pdf_text_and_streams(released(tmp_path / "o")[0])


def test_link_uri_is_scanned_and_removed(tmp_path):
    def setup(d, p):
        p.insert_text((72, 100), "contact", fontsize=11)
        p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 120, 104),
                       "uri": "mailto:link.leak@example.com"})
    src = native_pdf(tmp_path / "l.pdf", [], setup)
    doc = doc_of(src)
    assert any(u.unit_id == "page:1/link:0" for u in doc.units)
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    assert "link.leak" not in pdf_text_and_streams(released(tmp_path / "o")[0])


def test_blank_page_is_explicitly_ok_and_empty(tmp_path):
    d = pymupdf.open()
    d.new_page()
    d.save(str(tmp_path / "b.pdf"))
    doc = doc_of(tmp_path / "b.pdf")
    assert [(u.unit_id, u.text, u.status) for u in doc.units] == [("page:1", "", UnitStatus.OK)]
    assert doc.complete


# ---- OCR routing -------------------------------------------------------------------------------

def test_scanned_page_ocr_units_confidence_and_redaction(tmp_path, fake_ocr, capsys):
    src, words = scanned_pdf(tmp_path / "s.pdf", [(f"Mail {EMAIL}", 60, 100), ("Plain words here", 60, 200)])
    fake_ocr([("Mail", words[0][1], words[0][2], 30, words[0][4]),
              (EMAIL, words[0][1] + 40, words[0][2], words[0][3] - 40, words[0][4]), words[1]])
    doc = doc_of(src)
    ocr = [u for u in doc.units if u.unit_id == "page:1/ocr"]
    assert len(ocr) == 1 and ocr[0].method is ExtractionMethod.OCR
    assert ocr[0].ocr_confidence == 0.92 and ocr[0].status is UnitStatus.OK     # real mean, not invented
    assert any(f.unit_id == "page:1/ocr" and f.pii_type is PIIType.EMAIL for f in detect.detect(doc))
    out = tmp_path / "o"
    assert cli.main([str(src), "--out", str(out)]) == 0                           # re-OCR of redacted page found nothing
    rel = released(out)[0]
    d = pymupdf.open(str(rel))
    pix = d[0].get_pixmap(dpi=DPI)
    x = int((words[0][1] + 60) * pix.width / PAGE_W)
    y = int((words[0][2] + words[0][4] // 2) * pix.height / PAGE_H)
    assert pix.pixel(x, y)[:3] == (255, 255, 255)                                   # pixels really blanked
    assert capsys.readouterr().out.count(EMAIL) == 0


def test_mixed_native_and_scanned_pages(tmp_path, fake_ocr):
    src, words = scanned_pdf(tmp_path / "m.pdf", [(PHONE, 60, 100), ("Plain words here", 60, 200)],
                             native_first=f"Mail {EMAIL}")
    fake_ocr(words)         # a second, non-PII word keeps the redacted page from re-OCRing as empty
    doc = doc_of(src)
    ids = [u.unit_id for u in doc.units]
    assert "page:1" in ids and "page:2" in ids and "page:2/ocr" in ids and "page:1/ocr" not in ids
    found = {(f.unit_id, f.pii_type) for f in detect.detect(doc)}
    assert ("page:1", PIIType.EMAIL) in found and ("page:2/ocr", PIIType.PHONE) in found
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    blob = pdf_text_and_streams(released(tmp_path / "o")[0])
    assert "jane.tester" not in blob


def test_ocr_failure_is_unreadable_and_blocks(tmp_path, fake_ocr, monkeypatch, capsys):
    src, _ = scanned_pdf(tmp_path / "f.pdf", [("some text", 60, 100)])
    fake_ocr([])
    monkeypatch.setattr(pdf_extract, "ocr_words", lambda img: (_ for _ in ()).throw(RuntimeError("engine died")))
    doc = doc_of(src)
    u = next(u for u in doc.units if u.unit_id == "page:1/ocr")
    assert u.status is UnitStatus.UNREADABLE and not doc.complete and doc.warnings
    out = tmp_path / "o"
    assert cli.main([str(src), "--out", str(out)]) == 1
    assert released(out) == []


def test_low_confidence_and_empty_ocr_are_partial(tmp_path, fake_ocr):
    src, words = scanned_pdf(tmp_path / "c.pdf", [("Plain words here", 60, 100)])
    fake_ocr(words, conf=30.0)
    doc = doc_of(src)
    u = next(u for u in doc.units if u.unit_id == "page:1/ocr")
    assert u.status is UnitStatus.PARTIAL and u.ocr_confidence == 0.3 and not doc.complete
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 1
    fake_ocr([])
    u2 = next(u for u in doc_of(src).units if u.unit_id == "page:1/ocr")
    assert u2.status is UnitStatus.PARTIAL and u2.text == ""
    assert released(tmp_path / "o") == []


def test_missing_tesseract_fails_with_actionable_error_only_for_ocr_docs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pdf_extract, "ocr_available", lambda: False)
    src, _ = scanned_pdf(tmp_path / "s.pdf", [("some text", 60, 100)])
    out = tmp_path / "o"
    assert cli.main([str(src), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "Tesseract" in err and "winget" in err and released(out) == []
    assert cli.main([str(native_pdf(tmp_path / "n.pdf", [f"Mail {EMAIL}"])), "--out", str(out)]) == 0


def test_small_image_on_native_page_still_requires_ocr(tmp_path, fake_ocr, monkeypatch):
    png, words = png_page([("tiny", 5, 5)])

    def setup(d, p):
        p.insert_image(pymupdf.Rect(400, 700, 440, 720), stream=png)
    src = native_pdf(tmp_path / "i.pdf", [f"Mail {EMAIL}"], setup)
    monkeypatch.setattr(pdf_extract, "ocr_available", lambda: False)
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 2
    fake_ocr([])
    monkeypatch.setattr(pdf_extract, "ocr_available", lambda: True)
    assert any(u.unit_id == "page:1/ocr" for u in doc_of(src).units)


# ---- fail closed -------------------------------------------------------------------------------

def test_annotation_and_active_content_block_release(tmp_path):
    def with_annot(d, p):
        p.add_freetext_annot(pymupdf.Rect(72, 200, 300, 230), f"note {EMAIL}")
    src = native_pdf(tmp_path / "a.pdf", ["body"], with_annot)
    assert not doc_of(src).complete
    out = tmp_path / "o"
    assert cli.main([str(src), "--out", str(out)]) == 1 and released(out) == []

    def with_js(d, p):
        d.xref_set_key(d.pdf_catalog(), "OpenAction", "<</S/JavaScript/JS(app.alert\\(1\\))>>")
    src2 = native_pdf(tmp_path / "j.pdf", ["body"], with_js)
    assert not doc_of(src2).complete
    assert cli.main([str(src2), "--out", str(out)]) == 1 and released(out) == []


def test_review_finding_blocks_and_cleans_stale_pdf_artifacts(tmp_path):
    src = native_pdf(tmp_path / "r.pdf", ["Card 4111 1111 1111 1112", f"Mail {EMAIL}"])
    stem = ingest.load(src).doc_id[:12]
    out = tmp_path / "o"
    out.mkdir()
    for n in (f"{stem}.redacted.pdf", f"{stem}.UNSAFE-DRAFT.pdf", f"{stem}.audit.json"):
        (out / n).write_text("stale")
    (out / "notes.txt").write_text("keep")
    assert cli.main([str(src), "--out", str(out), "--audit"]) == 1
    assert sorted(p.name for p in out.iterdir() if p.suffix != ".json") == ["notes.txt"]
    assert not list(out.glob("*.audit.json")) and len(list(out.glob("*.report.json"))) == 1


def test_encrypted_and_corrupt_pdfs_are_rejected(tmp_path):
    d = pymupdf.open()
    d.new_page()
    d.save(str(tmp_path / "e.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    (tmp_path / "c.pdf").write_bytes(b"%PDF-1.4\nthis is not a pdf\n")
    out = tmp_path / "o"
    for name in ("e.pdf", "c.pdf"):
        assert cli.main([str(tmp_path / name), "--out", str(out)]) == 2
    assert released(out) == []


def test_redaction_refuses_changed_source(tmp_path):
    src = native_pdf(tmp_path / "x.pdf", [f"Mail {EMAIL}"])
    doc = doc_of(src)
    findings = detect.detect(doc)
    native_pdf(tmp_path / "x.pdf", ["different content"])
    with pytest.raises(ValueError):
        redact.redact(doc, findings, tmp_path / "o")


# ---- real Tesseract (skipped when it is not installed) -----------------------------------------

@pytest.mark.skipif(not pdf_extract.ocr_available(), reason="Tesseract not found: add it to PATH or set TESSERACT_CMD to tesseract.exe")
def test_real_tesseract_roundtrip(tmp_path):
    from PIL import ImageFont
    img = Image.new("RGB", (1700, 2200), "white")
    ImageDraw.Draw(img).text((120, 200), f"Email {EMAIL} please", fill="black",
                             font=ImageFont.load_default(size=60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(p.rect, stream=buf.getvalue())
    d.save(str(tmp_path / "real.pdf"))
    doc = doc_of(tmp_path / "real.pdf")
    assert any(f.pii_type is PIIType.EMAIL for f in detect.detect(doc))
    assert cli.main([str(tmp_path / "real.pdf"), "--out", str(tmp_path / "o")]) == 0
    out = released(tmp_path / "o")[0]
    assert "jane.tester" not in doc_text(out)
    assert not any(f.pii_type is PIIType.EMAIL for f in detect.detect(doc_of(out)))
