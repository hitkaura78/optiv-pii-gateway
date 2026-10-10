"""Text extraction, with OCR for scanned PDF pages. Owner: builder."""
from .models import ExtractedDocument, SourceDocument


def extract(src: SourceDocument) -> ExtractedDocument:
    """Return every text unit. Mark units PARTIAL/UNREADABLE and set complete=False
    rather than silently dropping content (images without OCR, failed pages, etc.)."""
    raise NotImplementedError
