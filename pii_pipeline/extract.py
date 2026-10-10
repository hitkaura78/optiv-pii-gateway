"""Text extraction, with OCR for scanned PDF pages. Owner: builder.

DOCX and PPTX go through the package scanner; PDF has its own module. The package scanner (package.py) turns every paragraph and every
other text/attribute node of every XML part into a unit. python-docx is used only to give
body/table/header/footer paragraphs their human-readable locators ("para:3",
"table:0/r1/c2/para:0", "section:0/header/para:0"); all other units are "pkg:<part>#...".
"""
import hashlib
import io
from typing import Iterator

from docx import Document
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from . import package
from .models import (
    ExtractedDocument, ExtractionMethod, SourceDocument, SourceKind, TextUnit, UnitStatus,
)


def _walk(container, prefix: str) -> Iterator[tuple[str, object]]:
    for i, p in enumerate(container.paragraphs):
        yield f"{prefix}para:{i}", p
    for t_i, table in enumerate(container.tables):
        seen = set()
        for r_i, row in enumerate(table.rows):
            for c_i, cell in enumerate(row.cells):
                if cell._tc in seen:      # merged cell appears once per grid column
                    continue
                seen.add(cell._tc)
                yield from _walk(cell, f"{prefix}table:{t_i}/r{r_i}/c{c_i}/")


def iter_paragraphs(docx) -> Iterator[tuple[str, object]]:
    """Stable (unit_id, paragraph) pairs for body, then headers/footers."""
    yield from _walk(docx, "")
    for s_i, section in enumerate(docx.sections):
        for name in ("header", "first_page_header", "even_page_header",
                     "footer", "first_page_footer", "even_page_footer"):
            part = getattr(section, name)
            if part.is_linked_to_previous:   # inherits; also avoids creating a part
                continue
            yield from _walk(part, f"section:{s_i}/{name.replace('_page', '')}/")


def _pptx_shapes(shapes, prefix: str) -> Iterator[tuple[str, object]]:
    for k, shape in enumerate(shapes):
        here = f"{prefix}/shape:{k}"
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _pptx_shapes(shape.shapes, here)
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            for j, p in enumerate(shape.text_frame.paragraphs):
                yield f"{here}/para:{j}", p
        if getattr(shape, "has_table", False) and shape.has_table:
            for r, row in enumerate(shape.table.rows):
                for c, cell in enumerate(row.cells):
                    for j, p in enumerate(cell.text_frame.paragraphs):
                        yield f"{here}/table/r{r}/c{c}/para:{j}", p


def iter_pptx_paragraphs(prs) -> Iterator[tuple[str, object]]:
    """Readable ids for slide shapes (incl. groups and tables) and speaker notes. Layouts, masters,
    and anything else get stable pkg:<part>#p<k> ids from the package scanner."""
    for i, slide in enumerate(prs.slides, 1):
        yield from _pptx_shapes(slide.shapes, f"slide:{i}")
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            for j, p in enumerate(slide.notes_slide.notes_text_frame.paragraphs):
                yield f"slide:{i}/notes/para:{j}", p


def scan_package(data: bytes, kind: SourceKind = SourceKind.DOCX) -> package.Scan:
    """Shared by extract and redact so both see identical units and ids."""
    try:
        names: dict[str, dict[str, str]] = {}
        if kind is SourceKind.PPTX:
            paragraphs = iter_pptx_paragraphs(Presentation(io.BytesIO(data)))
        else:
            paragraphs = iter_paragraphs(Document(io.BytesIO(data)))
        for uid, p in paragraphs:
            el = p._p
            part = str(p.part.partname).lstrip("/")
            names.setdefault(part, {})[el.getroottree().getpath(el)] = uid
        return package.scan(data, names)
    except Exception as exc:  # malformed package/XML
        raise ValueError(f"cannot parse {kind.value.upper()} ({type(exc).__name__})") from None


def extract(src: SourceDocument) -> ExtractedDocument:
    """Return every text unit. Mark units PARTIAL/UNREADABLE and set complete=False
    rather than silently dropping content (images without OCR, failed pages, etc.)."""
    if src.kind is SourceKind.PDF:
        from .pdf_extract import extract_pdf
        return extract_pdf(src)
    if src.kind not in (SourceKind.DOCX, SourceKind.PPTX):
        raise ValueError(f"{src.kind.value} extraction is not implemented")
    data = src.path.read_bytes()      # single read: hash check and parsing use the same bytes
    if hashlib.sha256(data).hexdigest() != src.doc_id:
        raise ValueError("file changed since ingestion")
    sc = scan_package(data, src.kind)
    units = [TextUnit(u.unit_id, u.text, ExtractionMethod.NATIVE, UnitStatus.OK) for u in sc.units]
    return ExtractedDocument(src, units, complete=not sc.warnings, warnings=list(sc.warnings))
