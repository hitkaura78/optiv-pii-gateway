"""Configuration and allow-list loader for Member 3 detection engine.

Loads settings from config/allowlist.yaml without requiring network calls or
external services. Uses PyYAML if installed, with a pure-Python YAML parser
fallback so the package runs with zero extra dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"
DEFAULT_ALLOWLIST_PATH = Path(__file__).resolve().parent.parent / "config" / "allowlist.yaml"


def _parse_scalar(val: str) -> Any:
    val = val.strip()
    if not val:
        return ""
    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
        return val[1:-1]
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def _simple_yaml_load(text: str) -> dict[str, Any]:
    """Pure-Python parser for structured YAML (mappings, nested mappings, lists of scalars/dicts)."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        # Strip full-line comments and trailing comments outside quotes
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        lines.append((indent, stripped))

    def parse_block(idx: int, base_indent: int) -> tuple[Any, int]:
        if idx >= len(lines):
            return {}, idx
        first_indent, first_content = lines[idx]
        if first_indent < base_indent:
            return {}, idx

        if first_content.startswith("- "):
            items: list[Any] = []
            while idx < len(lines):
                ind, content = lines[idx]
                if ind < base_indent or not content.startswith("- "):
                    break
                if ind > base_indent:
                    break
                rest = content[2:].strip()
                idx += 1
                if ":" in rest and not (rest.startswith("'") or rest.startswith('"')):
                    k, v = rest.split(":", 1)
                    obj: dict[str, Any] = {k.strip(): _parse_scalar(v)}
                    while idx < len(lines) and lines[idx][0] > ind:
                        sub_ind, sub_c = lines[idx]
                        if sub_c.startswith("- "):
                            break
                        if ":" in sub_c:
                            sk, sv = sub_c.split(":", 1)
                            obj[sk.strip()] = _parse_scalar(sv)
                        idx += 1
                    items.append(obj)
                else:
                    items.append(_parse_scalar(rest))
            return items, idx

        mapping: dict[str, Any] = {}
        while idx < len(lines):
            ind, content = lines[idx]
            if ind < base_indent:
                break
            if ind > base_indent:
                break
            if content.startswith("- "):
                break
            if ":" not in content:
                idx += 1
                continue
            key, val_part = content.split(":", 1)
            key = _parse_scalar(key.strip())
            val_part = val_part.strip()
            idx += 1
            if val_part:
                mapping[str(key)] = _parse_scalar(val_part)
            else:
                if idx < len(lines) and lines[idx][0] > ind:
                    child, idx = parse_block(idx, lines[idx][0])
                    mapping[str(key)] = child
                else:
                    mapping[str(key)] = {}
        return mapping, idx

    result, _ = parse_block(0, 0)
    return result if isinstance(result, dict) else {}


def load_yaml_file(path: Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
    except ImportError:
        pass
    return _simple_yaml_load(text)


@dataclass
class DetectSettings:
    mask_threshold: float = 0.50
    strict_threshold: float = 0.30
    low_ocr_confidence_threshold: float = 0.70
    dob_context_window_chars: int = 25
    last4_context_window_chars: int = 30
    family_context_window_chars: int = 25
    min_unique_surname_length: int = 5
    phone_regions: list[str] = field(
        default_factory=lambda: ["US", "GB", "IN", "FR", "PL", "SG", "SE", "HK"]
    )
    id_formats: dict[str, str] = field(default_factory=dict)


@dataclass
class AllowlistSettings:
    reference_id_prefixes: dict[str, str] = field(default_factory=dict)
    generic_mailboxes: list[dict[str, str]] = field(default_factory=list)
    company_names: list[dict[str, str]] = field(default_factory=list)
    hq_addresses: list[dict[str, str]] = field(default_factory=list)
    hotlines: list[dict[str, str]] = field(default_factory=list)
    money_amounts: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DetectorConfig:
    detect: DetectSettings = field(default_factory=DetectSettings)
    table_column_headers: dict[str, list[str]] = field(default_factory=dict)
    allowlist: AllowlistSettings = field(default_factory=AllowlistSettings)
    allowlist_file: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], allowlist_file: Path | None = None) -> DetectorConfig:
        det_raw = data.get("detect", {}) if isinstance(data.get("detect"), dict) else {}
        default_det = DetectSettings()
        det = DetectSettings(
            mask_threshold=float(det_raw.get("mask_threshold", default_det.mask_threshold)),
            strict_threshold=float(det_raw.get("strict_threshold", default_det.strict_threshold)),
            low_ocr_confidence_threshold=float(
                det_raw.get("low_ocr_confidence_threshold", default_det.low_ocr_confidence_threshold)
            ),
            dob_context_window_chars=int(
                det_raw.get("dob_context_window_chars", default_det.dob_context_window_chars)
            ),
            last4_context_window_chars=int(
                det_raw.get("last4_context_window_chars", default_det.last4_context_window_chars)
            ),
            family_context_window_chars=int(
                det_raw.get("family_context_window_chars", default_det.family_context_window_chars)
            ),
            min_unique_surname_length=int(
                det_raw.get("min_unique_surname_length", default_det.min_unique_surname_length)
            ),
            phone_regions=list(det_raw.get("phone_regions", default_det.phone_regions)),
            id_formats=dict(det_raw.get("id_formats", {})),
        )
        headers_raw = data.get("table_column_headers", {})
        headers = {str(k): [str(x) for x in v] for k, v in headers_raw.items()} if isinstance(headers_raw, dict) else {}

        allow_raw = data.get("allowlist", {}) if isinstance(data.get("allowlist"), dict) else {}
        ref_prefixes = dict(allow_raw.get("reference_id_prefixes", {}))
        allow = AllowlistSettings(
            reference_id_prefixes=ref_prefixes,
            generic_mailboxes=list(allow_raw.get("generic_mailboxes", [])),
            company_names=list(allow_raw.get("company_names", [])),
            hq_addresses=list(allow_raw.get("hq_addresses", [])),
            hotlines=list(allow_raw.get("hotlines", [])),
            money_amounts=list(allow_raw.get("money_amounts", [])),
        )
        return cls(detect=det, table_column_headers=headers, allowlist=allow, allowlist_file=allowlist_file)


def _load_default_yaml_merged() -> DetectorConfig:
    merged: dict[str, Any] = {}
    used_path: Path | None = None
    for candidate_path in (DEFAULT_ALLOWLIST_PATH, DEFAULT_CONFIG_PATH):
        if candidate_path.exists():
            raw = load_yaml_file(candidate_path)
            used_path = candidate_path
            for section, val in raw.items():
                if isinstance(val, dict) and isinstance(merged.get(section), dict):
                    merged[section].update(val)
                else:
                    merged[section] = val
    if used_path is not None:
        return DetectorConfig.from_dict(merged, used_path)
    return DetectorConfig()


def load_config(cfg: Any = None) -> DetectorConfig:
    """Resolve None, Path, dict, or custom object into a DetectorConfig."""
    if isinstance(cfg, DetectorConfig):
        return cfg
    if cfg is None:
        return _load_default_yaml_merged()
    if isinstance(cfg, (str, Path)):
        p = Path(cfg)
        return DetectorConfig.from_dict(load_yaml_file(p), p)
    if isinstance(cfg, dict):
        base = load_config(cfg.get("allowlist_file"))
        det_override = cfg.get("detect", {})
        if isinstance(det_override, dict):
            if "mask_threshold" in det_override:
                base.detect.mask_threshold = float(det_override["mask_threshold"])
            if "strict_threshold" in det_override:
                base.detect.strict_threshold = float(det_override["strict_threshold"])
            if "low_ocr_confidence_threshold" in det_override:
                base.detect.low_ocr_confidence_threshold = float(det_override["low_ocr_confidence_threshold"])
            if "id_formats" in det_override and isinstance(det_override["id_formats"], dict):
                base.detect.id_formats = dict(det_override["id_formats"])
            if "phone_regions" in det_override and isinstance(det_override["phone_regions"], list):
                base.detect.phone_regions = list(det_override["phone_regions"])
        if "table_column_headers" in cfg and isinstance(cfg["table_column_headers"], dict):
            base.table_column_headers = {str(k): list(v) for k, v in cfg["table_column_headers"].items()}
        if "allowlist" in cfg and isinstance(cfg["allowlist"], dict):
            al = cfg["allowlist"]
            if "reference_id_prefixes" in al:
                base.allowlist.reference_id_prefixes = dict(al["reference_id_prefixes"])
            if "generic_mailboxes" in al:
                base.allowlist.generic_mailboxes = list(al["generic_mailboxes"])
            if "company_names" in al:
                base.allowlist.company_names = list(al["company_names"])
            if "hq_addresses" in al:
                base.allowlist.hq_addresses = list(al["hq_addresses"])
            if "hotlines" in al:
                base.allowlist.hotlines = list(al["hotlines"])
            if "money_amounts" in al:
                base.allowlist.money_amounts = list(al["money_amounts"])
        return base
    # Duck-typed config object (e.g. SimpleNamespace with .detect.id_formats)
    base = load_config(getattr(cfg, "allowlist_file", None))
    det_attr = getattr(cfg, "detect", None)
    if det_attr is not None:
        for attr in (
            "mask_threshold",
            "strict_threshold",
            "low_ocr_confidence_threshold",
            "dob_context_window_chars",
            "last4_context_window_chars",
            "family_context_window_chars",
            "min_unique_surname_length",
            "phone_regions",
            "id_formats",
        ):
            if hasattr(det_attr, attr):
                val = getattr(det_attr, attr)
                if attr == "id_formats" and not isinstance(val, dict):
                    val = vars(val)
                setattr(base.detect, attr, val)
    return base

