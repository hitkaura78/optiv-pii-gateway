"""PDF text extraction: native text via PyMuPDF, Tesseract OCR for image pages. Owner: builder.

Unit ids (1-based pages):
  page:N          native text layer of page N (always present, may be empty)
  page:N/ocr      OCR of the rendered page, added when the page is image-dominated or sparse
  page:N/link:K   target URI of the K-th link on page N

A page that needs OCR but cannot get a trustworthy result is never reported as OK:
  * OCR engine error on that page      -> UNREADABLE unit, extraction incomplete
  * no words recognised                -> PARTIAL unit, extraction incomplete
  * mean word confidence < LOW_CONF    -> PARTIAL unit, extraction incomplete
If Tesseract is not installed and some page needs OCR, extraction raises ValueError with
install instructions (native-only PDFs never touch OCR).

Geometry needed for redaction (character / word rectangles in unrotated page coordinates) is not
part of the shared contract: ``extract_with_geometry`` returns it, and redaction always recomputes
it from the hashed source bytes and fails if the units differ from the ones it was given.
"""
import hashlib
import io
import re
from dataclasses import dataclass

import pymupdf

from .models import (
    ExtractedDocument, ExtractionMethod, SourceDocument, SourceKind, TextUnit, UnitStatus,
)

MAX_PAGES = 300
MAX_RENDER_PIXELS = 40_000_000
OCR_DPI = 300
OCR_TIMEOUT_S = 120
LOW_CONF = 0.60            # mean word confidence (0..1) below this -> PARTIAL
SPARSE_CHARS = 20          # fewer non-space native characters than this = "no real text layer"
VECTOR_TEXT_SEGMENTS = 10  # this many vector segments on a page = possibly outlined text -> OCR the page
INSTALL_HINT = ("Tesseract OCR is required for image/scanned pages but was not found. Install it "
                "(winget install --id UB-Mannheim.TesseractOCR), then either add its folder to PATH and open a "
                "new terminal, or set TESSERACT_CMD to the full path of tesseract.exe. Check `tesseract --version`.")


@dataclass
class OcrWord:
    text: str
    conf: float          # 0..100 as reported by the engine
    left: int
    top: int
    width: int
    height: int
    line: tuple          # words with the same key are on one line (block, paragraph, line)


@dataclass
class Geometry:
    kind: str                                   # "native" | "ocr" | "link"
    segs: list                                  # [(start, end, pymupdf.Rect | None)]
    link_index: int | None = None


# ---- OCR engine (module-level so tests can substitute a fake) -------------------------------

def _tesseract():
    """pytesseract, pointed at $TESSERACT_CMD if set; otherwise it resolves `tesseract` on PATH."""
    import os
    import pytesseract
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def ocr_available() -> bool:
    try:
        _tesseract().get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_words(image) -> list[OcrWord]:
    pytesseract = _tesseract()
    d = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, timeout=OCR_TIMEOUT_S)
    words = []
    for i, t in enumerate(d["text"]):
        if not t.strip():
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        words.append(OcrWord(t.strip(), conf, int(d["left"][i]), int(d["top"][i]), int(d["width"][i]),
                             int(d["height"][i]), (d["block_num"][i], d["par_num"][i], d["line_num"][i])))
    return words


# ---- native text -------------------------------------------------------------------------------

def _native(page) -> tuple[str, list, bool]:
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE)
    chars: list[str] = []
    segs: list = []
    outside = False
    pr = page.rect
    for block in raw["blocks"]:
        if block.get("type") != 0:
            continue
        for li, line in enumerate(block["lines"]):
            if li and chars and chars[-1] not in "-@./_":    # a break after - @ . / _ joins without a gap
                segs.append((len(chars), len(chars) + 1, None))
                chars.append(" ")
            for span in line["spans"]:
                for ch in span["chars"]:
                    r = pymupdf.Rect(ch["bbox"])
                    if not r.intersects(pr):
                        outside = True
                    c = ch["c"] or " "
                    segs.append((len(chars), len(chars) + len(c), r))
                    chars.append(c)
        if chars and chars[-1] != "\n":
            segs.append((len(chars), len(chars) + 1, None))
            chars.append("\n")
    return "".join(chars).rstrip("\n"), segs, outside


def _image_fraction(page) -> float:
    area = page.rect.get_area() or 1.0
    total = 0.0
    for info in page.get_image_info():
        total += (pymupdf.Rect(info["bbox"]) & page.rect).get_area()
    return min(1.0, total / area)


def _needs_ocr(page, text: str) -> bool:
    if _image_fraction(page) > 0:      # any visible image may contain text: never assume it does not
        return True
    # possibly outlined (vector) text, whether or not the page also has a real text layer
    # count segments, not paths: outlining tools often emit a whole word or line as ONE compound path
    return sum(len(d["items"]) for d in page.get_drawings()) >= VECTOR_TEXT_SEGMENTS


# ---- OCR ---------------------------------------------------------------------------------------

def _render(page):
    from PIL import Image
    zoom = OCR_DPI / 72.0
    w, h = page.rect.width * zoom, page.rect.height * zoom
    if w * h > MAX_RENDER_PIXELS:
        zoom *= (MAX_RENDER_PIXELS / (w * h)) ** 0.5
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), zoom


def _ocr_unit(page, uid: str, warnings: list[str], pno: int) -> tuple[TextUnit, Geometry | None]:
    try:
        image, zoom = _render(page)
        words = ocr_words(image)
    except Exception as exc:          # engine crash/timeout on this page: explicit, never "OK"
        warnings.append(f"page {pno}: OCR failed ({type(exc).__name__})")
        return TextUnit(uid, "", ExtractionMethod.OCR, UnitStatus.UNREADABLE), None
    chars: list[str] = []
    segs: list = []
    prev = None
    to_page = page.derotation_matrix
    for w in words:
        if prev is not None:
            sep = " " if w.line == prev else "\n"
            segs.append((len(chars), len(chars) + 1, None))
            chars.append(sep)
        rect = (pymupdf.Rect(w.left / zoom, w.top / zoom, (w.left + w.width) / zoom, (w.top + w.height) / zoom)
                * to_page).normalize()
        segs.append((len(chars), len(chars) + len(w.text), rect))
        chars.extend(w.text)
        prev = w.line
    text = "".join(chars)
    if not words:
        warnings.append(f"page {pno}: OCR recognised no text on an image page")
        return TextUnit(uid, "", ExtractionMethod.OCR, UnitStatus.PARTIAL), Geometry("ocr", [])
    mean = sum(w.conf for w in words) / len(words) / 100.0
    status = UnitStatus.OK
    if mean < LOW_CONF:
        status = UnitStatus.PARTIAL
        warnings.append(f"page {pno}: low OCR confidence")
    return TextUnit(uid, text, ExtractionMethod.OCR, status, round(mean, 3)), Geometry("ocr", segs)


# ---- document ----------------------------------------------------------------------------------

def open_pdf(data: bytes):
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ValueError("cannot parse PDF") from None
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise ValueError("encrypted PDF is not supported")
    if doc.page_count > MAX_PAGES:
        doc.close()
        raise ValueError(f"PDF has more than {MAX_PAGES} pages")
    return doc


_MARKED = re.compile(rb"/(?:ActualText|Alt|E)\s*[(<]")


def _marked_content(doc, page) -> bool:
    """ActualText/Alt strings inside content streams (tagged PDFs) cannot be scanned or redacted."""
    streams = [page.read_contents()]
    try:
        streams += [doc.xref_stream(x[0]) or b"" for x in page.get_xobjects()]
    except Exception:
        pass
    return any(_MARKED.search(b) for b in streams if b)


_ACTIVE = ("/JavaScript", "/JS", "/OCProperties", "/OpenAction", "/Launch", "/EmbeddedFile", "/XFA", "/AA")


def _active_content(doc) -> list[str]:
    found = set()
    for x in range(1, min(doc.xref_length(), 200_000)):
        try:
            obj = doc.xref_object(x, compressed=False)
        except Exception:
            continue
        for key in _ACTIVE:
            i = obj.find(key)
            if i >= 0 and not obj[i + len(key):i + len(key) + 1].isalnum():
                found.add(key)
    msgs = []
    if found & {"/JavaScript", "/JS", "/OpenAction", "/Launch", "/AA", "/XFA"}:
        msgs.append("JavaScript, actions or forms-XML present (not scanned)")
    if "/OCProperties" in found:
        msgs.append("optional-content (hidden layer) data present (not scanned)")
    if "/EmbeddedFile" in found:
        msgs.append("embedded files present (not scanned)")
    return msgs


def extract_pdf(src: SourceDocument) -> ExtractedDocument:
    return extract_with_geometry(src)[0]


def extract_with_geometry(src: SourceDocument) -> tuple[ExtractedDocument, dict]:
    if src.kind is not SourceKind.PDF:
        raise ValueError("not a PDF")
    data = src.path.read_bytes()
    if hashlib.sha256(data).hexdigest() != src.doc_id:
        raise ValueError("file changed since ingestion")
    doc = open_pdf(data)
    units: list[TextUnit] = []
    geometry: dict[str, Geometry] = {}
    warnings: list[str] = []
    try:
        for i, page in enumerate(doc):
            pno = i + 1
            text, segs, outside = _native(page)
            units.append(TextUnit(f"page:{pno}", text, ExtractionMethod.NATIVE, UnitStatus.OK))
            geometry[f"page:{pno}"] = Geometry("native", segs)
            if outside:
                warnings.append(f"page {pno}: text outside the page area (not redactable)")
            if _needs_ocr(page, text):
                if not ocr_available():
                    raise ValueError(f"page {pno} needs OCR. {INSTALL_HINT}")
                unit, geo = _ocr_unit(page, f"page:{pno}/ocr", warnings, pno)
                units.append(unit)
                if geo is not None:
                    geometry[unit.unit_id] = geo
            if _marked_content(doc, page):
                warnings.append(f"page {pno}: tagged marked-content text (ActualText/Alt) present (not scanned)")
            if any(a.type[0] != pymupdf.PDF_ANNOT_LINK for a in page.annots()) or any(True for _ in page.widgets()):
                warnings.append(f"page {pno}: annotations or form fields present (not scanned)")
            for k, link in enumerate(page.get_links()):
                uri = link.get("uri")
                if uri:
                    uid = f"page:{pno}/link:{k}"
                    units.append(TextUnit(uid, uri, ExtractionMethod.NATIVE, UnitStatus.OK))
                    geometry[uid] = Geometry("link", [], k)
        warnings.extend(_active_content(doc))
        if doc.get_toc():
            warnings.append("outline/bookmarks present (not scanned)")
    finally:
        doc.close()
    complete = not warnings and all(u.status is UnitStatus.OK for u in units)
    return ExtractedDocument(src, units, complete, warnings), geometry
