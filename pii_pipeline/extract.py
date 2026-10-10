"""Text extraction, with OCR for scanned PDF pages. Owner: builder.

Only DOCX is implemented. The package scanner (package.py) turns every paragraph and every
other text/attribute node of every XML part into a unit. python-docx is used only to give
body/table/header/footer paragraphs their human-readable locators ("para:3",
"table:0/r1/c2/para:0", "section:0/header/para:0"); all other units are "pkg:<part>#...".
"""
import hashlib
import io
from typing import Iterator

from docx import Document

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


def scan_package(data: bytes) -> package.Scan:
    """Shared by extract and redact so both see identical units and ids."""
    try:
        names: dict[str, dict[str, str]] = {}
        for uid, p in iter_paragraphs(Document(io.BytesIO(data))):
            part = str(p.part.partname).lstrip("/")
            names.setdefault(part, {})[p._p.getroottree().getpath(p._p)] = uid
        return package.scan(data, names)
    except Exception as exc:  # malformed package/XML
        raise ValueError(f"cannot parse DOCX ({type(exc).__name__})") from None


def extract(src: SourceDocument) -> ExtractedDocument:
    """Return every text unit. Mark units PARTIAL/UNREADABLE and set complete=False
    rather than silently dropping content (images without OCR, failed pages, etc.)."""
    if src.kind is not SourceKind.DOCX:
        raise ValueError(f"{src.kind.value} extraction is not implemented")
    data = src.path.read_bytes()      # single read: hash check and parsing use the same bytes
    if hashlib.sha256(data).hexdigest() != src.doc_id:
        raise ValueError("file changed since ingestion")
    sc = scan_package(data)
    units = [TextUnit(u.unit_id, u.text, ExtractionMethod.NATIVE, UnitStatus.OK) for u in sc.units]
    return ExtractedDocument(src, units, complete=not sc.warnings, warnings=list(sc.warnings))
