"""Member 3 PII Detection Package (`detect/`)."""
from .config import DetectorConfig, load_config
from .engine import detect_and_resolve, detect_strict
from .models import Candidate, DetectedFinding
from .registry import NameRegistry, variants

__all__ = [
    "Candidate",
    "DetectedFinding",
    "DetectorConfig",
    "NameRegistry",
    "detect_and_resolve",
    "detect_strict",
    "load_config",
    "variants",
]

