"""Ingestion: validate an input file and identify it. Owner: builder."""
from pathlib import Path

from .models import SourceDocument


def load(path: Path) -> SourceDocument:
    """Check existence, size and type (by magic bytes, not just extension); hash the bytes.
    Raise ValueError for unsupported/corrupt input."""
    raise NotImplementedError
