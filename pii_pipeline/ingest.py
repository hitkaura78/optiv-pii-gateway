"""Ingestion: validate an input file and identify it. Owner: builder."""
import hashlib
import zipfile
from pathlib import Path

from .models import SourceDocument, SourceKind

MAX_BYTES = 25 * 1024 * 1024            # compressed file size limit
MAX_UNCOMPRESSED = 200 * 1024 * 1024    # zip-bomb guard
MAX_ZIP_MEMBERS = 5000

_EXT = {".pdf": SourceKind.PDF, ".docx": SourceKind.DOCX, ".pptx": SourceKind.PPTX}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # legacy .doc/.ppt or password-encrypted OOXML


def _sniff_zip(path: Path) -> SourceKind:
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
                raise ValueError("archive exceeds size limits")
            if z.testzip() is not None:
                raise ValueError("corrupt archive")
            names = set(z.namelist())
    except zipfile.BadZipFile:
        raise ValueError("corrupt or truncated archive") from None
    if "[Content_Types].xml" in names:
        if "word/document.xml" in names:
            return SourceKind.DOCX
        if "ppt/presentation.xml" in names:
            return SourceKind.PPTX
    raise ValueError("unsupported archive type")


def load(path: Path) -> SourceDocument:
    """Check existence, size and type (by magic bytes, not just extension); hash the bytes.
    Raise ValueError for unsupported/corrupt input."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("input is not a readable file")
    size = path.stat().st_size
    if size == 0:
        raise ValueError("file is empty")
    if size > MAX_BYTES:
        raise ValueError(f"file exceeds {MAX_BYTES} byte limit")
    with path.open("rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"file exceeds {MAX_BYTES} byte limit")

    if data.startswith(b"PK\x03\x04"):
        kind = _sniff_zip(path)
    elif b"%PDF-" in data[:1024]:
        kind = SourceKind.PDF
    elif data.startswith(_OLE):
        raise ValueError("legacy or encrypted Office file is not supported")
    else:
        raise ValueError("unsupported file type")

    expected = _EXT.get(path.suffix.lower())
    if expected is not None and expected is not kind:
        raise ValueError("file extension does not match file content")
    return SourceDocument(path=path, kind=kind, doc_id=hashlib.sha256(data).hexdigest())
