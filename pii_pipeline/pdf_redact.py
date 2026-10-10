"""PDF redaction with PyMuPDF true redaction. Owner: builder.

Text under each finding's rectangles is removed from the content stream (not just covered), image
pixels under them are blanked, and link targets with findings are deleted. Geometry is always
recomputed from the hashed source bytes (re-running OCR for image pages) and must reproduce the
unit texts that produced the findings, otherwise redaction refuses (ValueError).

Everything that is not page content is reduced to an allowlist (catalog, page and link keys; no
structure tree, thumbnails, XMP, piece info, alt text), links are rebuilt with only Rect/Subtype/
A(URI or GoTo), and the *saved* file is re-opened and checked (``verify_saved``).
"""
import hashlib
import json
import re
from pathlib import Path

import pymupdf

from .models import Action, ExtractedDocument, Finding, RedactionResult, SourceKind
from .pdf_extract import extract_with_geometry, open_pdf

_UNIT = re.compile(r"page:(\d+)(?:/(ocr|link:(\d+)))?$")

_CATALOG_OK = {"Type", "Pages", "Version", "PageLayout", "PageMode"}
_PAGE_OK = {"Type", "Parent", "MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox", "Rotate",
            "Resources", "Contents", "Annots", "UserUnit", "Group"}
_LINK_OK = {"Type", "Subtype", "Rect", "Border", "BS", "A", "Dest", "NM"}
_A_NAMES = {"S", "URI", "GoTo", "D", "XYZ", "Fit", "FitH", "FitV", "FitR", "FitB", "FitBH", "FitBV"}
_BANNED_ANYWHERE = {"Metadata", "PieceInfo", "Thumb", "StructTreeRoot", "MarkInfo", "Alt", "ActualText"}
_STRING = re.compile(r"\((?:\\.|[^\\()])*\)")


def _line_groups(rects: list) -> list:
    """Union consecutive rectangles that sit on the same line."""
    out: list = []
    for r in rects:
        if out:
            last = out[-1]
            overlap = min(last.y1, r.y1) - max(last.y0, r.y0)
            if overlap > 0.5 * min(last.height, r.height):
                out[-1] = last | r
                continue
        out.append(pymupdf.Rect(r))
    return out


def _keys(pdf, xref: int) -> set:
    """Keys whose value is not PDF null (xref_set_key(..., "null") leaves the key listed but empty)."""
    return {k for k in pdf.xref_get_keys(xref) if pdf.xref_get_key(xref, k)[0] != "null"}


def _sanitize_structure(pdf, dropped: dict) -> None:
    """Reduce catalog, pages and links to an allowlist; drop tags, thumbnails, XMP and similar."""
    cat = pdf.pdf_catalog()
    for key in _keys(pdf, cat):
        if key not in _CATALOG_OK and key != "Info":     # "Info" is MuPDF's own Producer entry; checked in verify_saved
            pdf.xref_set_key(cat, key, "null")
    for x in range(1, pdf.xref_length()):
        try:
            keys = _keys(pdf, x)
        except Exception:
            continue
        for key in keys & _BANNED_ANYWHERE:
            pdf.xref_set_key(x, key, "null")
    for page in pdf:
        for key in _keys(pdf, page.xref):
            if key not in _PAGE_OK:
                pdf.xref_set_key(page.xref, key, "null")
        links = page.get_links()
        keep = []
        for k, link in enumerate(links):
            if link.get("kind") in (pymupdf.LINK_URI, pymupdf.LINK_GOTO) and k not in dropped.get(page.number + 1, set()):
                keep.append({kk: vv for kk, vv in link.items() if kk in ("kind", "from", "uri", "page", "to", "zoom")})
        for link in reversed(links):
            page.delete_link(link)
        for link in keep:                      # fresh annotations: only Rect/Subtype/A (+BS/NM)
            page.insert_link(link)


def verify_saved(path: Path) -> None:
    """Re-open the saved bytes and fail unless catalog, pages and links hold allowlisted keys only."""
    pdf = pymupdf.open(str(path))
    try:
        cat = pdf.pdf_catalog()
        if not _keys(pdf, cat) <= _CATALOG_OK | {"Info"}:
            raise ValueError("catalog holds non-allowlisted keys")
        kind, info = pdf.xref_get_key(cat, "Info")
        if kind != "null" and (kind != "dict" or set(re.findall(r"/(\w+)", _STRING.sub("", info))) - {"Producer"}):
            raise ValueError("document info holds more than the producer string")
        for x in range(1, pdf.xref_length()):
            try:
                keys = _keys(pdf, x)
            except Exception:
                continue
            if keys & _BANNED_ANYWHERE:
                raise ValueError("PDF still holds tag/thumbnail/metadata entries")
        for page in pdf:
            if not _keys(pdf, page.xref) <= _PAGE_OK:
                raise ValueError("page holds non-allowlisted keys")
            for annot in page.annots() or []:
                x = annot.xref
                if not _keys(pdf, x) <= _LINK_OK or pdf.xref_get_key(x, "Subtype")[1] != "/Link":
                    raise ValueError("annotation holds non-allowlisted keys")
                nm = pdf.xref_get_key(x, "NM")
                if nm[0] != "null" and not re.fullmatch(r"\(fitz-L\d+\)", nm[1]):
                    raise ValueError("annotation name not allowlisted")
                kind, val = pdf.xref_get_key(x, "A")
                if kind not in ("null", "dict") or (
                        kind == "dict" and not set(re.findall(r"/(\w+)", _STRING.sub("", val))) <= _A_NAMES):
                    raise ValueError("link action not allowlisted")
    finally:
        pdf.close()


def redact_pdf(doc: ExtractedDocument, findings: list[Finding], out_dir: Path, write_audit: bool = False) -> RedactionResult:
    """Write <doc_id12>.UNSAFE-DRAFT.pdf. Caller must re-scan and gate it before release."""
    if doc.source.kind is not SourceKind.PDF:
        raise ValueError("not a PDF")
    data = doc.source.path.read_bytes()
    if hashlib.sha256(data).hexdigest() != doc.source.doc_id:
        raise ValueError("source file changed since extraction")
    fresh, geometry = extract_with_geometry(doc.source)
    expected = {u.unit_id: u.text for u in doc.units}
    if {u.unit_id: u.text for u in fresh.units} != expected:
        raise ValueError("source content does not match extraction")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    page_rects: dict[int, list] = {}
    drop_links: dict[int, set] = {}
    audit: dict[str, str] = {}
    applied: list[str] = []
    for f in findings:
        if f.action not in (Action.REDACT, Action.MASK):
            continue
        m = _UNIT.match(f.unit_id)
        geo = geometry.get(f.unit_id)
        if not m or geo is None:
            raise ValueError("finding location not found in PDF")
        pno = int(m.group(1))
        text = expected[f.unit_id]
        s, e = (0, len(text)) if f.start is None else (f.start, f.end)
        audit[f.finding_id] = text[s:e]
        applied.append(f.finding_id)
        if geo.kind == "link":
            drop_links.setdefault(pno, set()).add(geo.link_index)
            continue
        rects = [r for (a, b, r) in geo.segs if r is not None and a < e and b > s]
        if not rects:
            raise ValueError("finding could not be mapped to page geometry")
        for r in _line_groups(rects):
            if geo.kind == "ocr":      # image pixels: pad so no glyph edge survives; white fill because
                # black bars make Tesseract misread the rest of the page (confidence collapses)
                r = pymupdf.Rect(r.x0 - 1.5, r.y0 - 1.5, r.x1 + 1.5, r.y1 + 1.5)
                fill = (1, 1, 1)
            else:                      # text layer: inset vertically so neighbouring lines are untouched
                r = pymupdf.Rect(r.x0, r.y0 + r.height * 0.1, r.x1, r.y1 - r.height * 0.1)
                fill = (0, 0, 0)
            page_rects.setdefault(pno, []).append((r, fill))

    stem = doc.source.doc_id[:12]
    output_path = out_dir / f"{stem}.UNSAFE-DRAFT.pdf"
    pdf = open_pdf(data)
    try:
        for pno in sorted(page_rects):
            page = pdf[pno - 1]
            for r, fill in page_rects[pno]:
                page.add_redact_annot(r, fill=fill)
            has_ocr = any(fill == (1, 1, 1) for _, fill in page_rects[pno])
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED if has_ocr else pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        pdf.set_metadata({})
        pdf.del_xml_metadata()
        _sanitize_structure(pdf, drop_links)      # after set_metadata, which re-creates an Info entry
        pdf.save(str(output_path), garbage=4, deflate=True, clean=True)   # full rewrite, never incremental
    finally:
        pdf.close()
    try:
        verify_saved(output_path)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    audit_path = None
    if write_audit:
        audit_path = out_dir / f"{stem}.audit.json"
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
    return RedactionResult(output_path=output_path, applied=applied, audit_path=audit_path)
