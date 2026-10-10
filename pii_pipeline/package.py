"""Office-package (OPC zip) inspection and sanitising for DOCX. Owner: builder.

Every XML part is parsed and split into *units*, each a list of "pieces" (text, setter):

* Paragraph units: every w:p / a:p in every part (body, headers/footers, glossary, footnotes,
  block-level customXml, ...). Pieces are the paragraph's w:t, w:instrText, w:delText, a:t and
  m:t nodes in document order, plus literal tab/break separators, so text split across nodes
  is detected as one string. A span found in the joined text is mapped back to the exact nodes
  and characters, and every fragment is rewritten (see ``apply_spans``).
* Node units: every other text node/tail (grouped per element across empty children) and every
  attribute value, e.g. relationship targets, docVars, comments.

Supported text model (anything else fails closed): paragraphs as above; and, in any other
part, at most ONE separate text-bearing element (so a value cannot be split across sibling
fields we would have to join without knowing which fields belong together). docProps/core.xml and
docProps/app.xml are exempt because redaction rewrites them to dates / numeric statistics only.
Content outside the root element (comments, PIs) is reported as units and dropped on write; a
DOCTYPE is rejected. Every XML part is re-serialised from its checked tree on output.

Coverage is per element: each text-bearing element is either inside a paragraph unit or becomes
a node unit. Structures that cannot be mapped (children inside a text element) add a warning,
which marks extraction incomplete. Numbering depends only on a part's XML, so extract and redact
agree on unit ids.
"""
import io
import posixpath
import re
import zipfile

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_P_TAGS = {f"{{{W}}}p", f"{{{A}}}p"}
_WTEXT = {f"{{{W}}}t", f"{{{W}}}instrText", f"{{{W}}}delText"}
_TEXT_TAGS = _WTEXT | {f"{{{A}}}t", f"{{{M}}}t"}
_LITERAL = {f"{{{W}}}tab": "\t", f"{{{W}}}br": "\n", f"{{{W}}}cr": "\n", f"{{{W}}}noBreakHyphen": "-",
            f"{{{A}}}br": "\n"}
_HEX = re.compile(r"[0-9A-Fa-f]{1,8}")   # rsids, paraIds, colours: cannot hold a detectable value
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_CORE_KEEP = {"created", "modified"}
# Font signature/panose and revision-id attributes are fixed-format codes, not content.
_CODE_ELEMS = {"panose1", "sig", "creationId", "modId"}
_CODE_ATTRS = {"panose1", "csb0", "csb1", "usb0", "usb1", "usb2", "usb3", "paraId", "textId"}
_DRAWING = re.compile(rb"<w:(?:drawing|pict|object)[ >/]")
# PowerPoint content we cannot read: pictures/blips, OLE, media, controls
_PPT_UNREAD = re.compile(rb"<(?:p:pic|a:blip|p:oleObj|p:video|a:videoFile|a:audioFile|a:wavAudioFile|p:control)[ >/]")
_PPT_UNREAD_DIRS = ("ppt/charts/", "ppt/diagrams/", "ppt/activeX/", "ppt/embeddings/", "ppt/drawings/")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})?")
_INT, _BOOL = re.compile(r"\d{1,12}"), re.compile(r"true|false")
_APP_KEEP = {**dict.fromkeys(["Pages", "Words", "Characters", "Lines", "Paragraphs", "CharactersWithSpaces",
                              "TotalTime", "DocSecurity"], _INT),
             **dict.fromkeys(["ScaleCrop", "LinksUpToDate", "SharedDoc", "HyperlinksChanged"], _BOOL)}
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
# Parts whose attributes are independent schema fields (enumerations, ids, formatting), so each
# attribute may be scanned on its own. Everything else is "unknown": all its values count as
# text fields and more than one blocks release.
# Office bibliography data (customXml sources) is schema-defined too: attributes are independent fields.
_KNOWN_ROOT_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/bibliography}"
_KNOWN_PARTS = re.compile(
    r"\[Content_Types\]\.xml|.*\.rels|docProps/(?:core|app)\.xml"
    r"|word/(?:document|styles|stylesWithEffects|settings|webSettings|fontTable|numbering|footnotes|endnotes"
    r"|comments|commentsExtended|commentsIds|people|header\d*|footer\d*)\.xml"
    r"|word/theme/[^/]+\.xml|word/glossary/.*\.xml|customXml/itemProps\d*\.xml"
    r"|ppt/(?:presentation|presProps|viewProps|tableStyles)\.xml"
    r"|ppt/slides/slide\d+\.xml|ppt/slideLayouts/slideLayout\d+\.xml|ppt/slideMasters/slideMaster\d+\.xml"
    r"|ppt/notesSlides/notesSlide\d+\.xml|ppt/notesMasters/notesMaster\d+\.xml"
    r"|ppt/handoutMasters/handoutMaster\d+\.xml|ppt/theme/theme\d+\.xml")
_EXEMPT_PARTS = {"docProps/core.xml", "docProps/app.xml"}
_PARSER = dict(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


class Unit:
    __slots__ = ("unit_id", "part", "pieces")

    def __init__(self, unit_id: str, part: str, pieces: list):
        self.unit_id, self.part, self.pieces = unit_id, part, pieces

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.pieces)


class Scan:
    def __init__(self, units, warnings, roots, order):
        self.units, self.warnings, self.roots, self.order = units, warnings, roots, order


def is_xml_part(name: str) -> bool:
    return name.endswith(".xml") or name.endswith(".rels")


_REMOVED = ("docProps/custom.xml", "word/people.xml", "ppt/commentAuthors.xml", "ppt/authors.xml",
            "ppt/revisionInfo.xml")


def should_remove(name: str) -> bool:
    """Parts dropped on redaction: custom props, reviewer lists, review comments (classic and
    modern PowerPoint comments, removed rather than redacted), printer settings (printer/user names),
    PowerPoint 365 revision/change history (client GUIDs, no slide content), page-preview thumbnail."""
    return name in _REMOVED or name.startswith(("docProps/thumbnail", "ppt/comments/", "ppt/changesInfos/", "ppt/printerSettings/",
                                         "word/printerSettings/"))


def parse_xml(blob: bytes):
    try:
        root = etree.fromstring(blob, etree.XMLParser(**_PARSER))
    except etree.XMLSyntaxError:
        raise ValueError("malformed XML part") from None
    docinfo = root.getroottree().docinfo
    if docinfo.doctype or docinfo.internalDTD is not None or docinfo.externalDTD is not None:
        raise ValueError("XML part with DOCTYPE is not supported")
    return root


def serialize(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _set_text(el, preserve=False):
    def setter(nv):
        el.text = nv
        if preserve:
            el.set(_XML_SPACE, "preserve")
    return setter


def _set_tail(el):
    return lambda nv: setattr(el, "tail", nv)


def _set_attr(el, k):
    return lambda nv: el.set(k, nv)


def _paragraph_pieces(p, claimed: set, problems: list) -> list:
    pieces: list = []

    def walk(el):
        for ch in el:
            tag = ch.tag
            if not isinstance(tag, str) or tag in _P_TAGS:   # nested paragraphs are their own units
                continue
            if tag in _TEXT_TAGS:
                if len(ch):
                    problems.append("text node with nested elements (cannot be mapped for redaction)")
                claimed.add(ch)
                pieces.append((ch.text or "", _set_text(ch, tag in _WTEXT)))
            elif tag in _LITERAL:
                pieces.append((_LITERAL[tag], None))
            else:
                walk(ch)

    walk(p)
    return pieces


def _scan_part(name: str, root, names: dict, units: list, warnings: set) -> None:
    tree = root.getroottree()
    named = names.get(name, {})
    claimed: set = set()
    problems: list = []
    for k, p in enumerate(root.iter(*_P_TAGS)):
        pieces = _paragraph_pieces(p, claimed, problems)
        uid = named.get(tree.getpath(p)) if named else None
        if uid is None:
            uid = f"pkg:{name}#p{k}"
            if not any(t for t, _ in pieces):
                continue
        units.append(Unit(uid, name, pieces))
    if problems:
        warnings.add(problems[0])

    for i, sib in enumerate(list(root.itersiblings(preceding=True)) + list(root.itersiblings())):
        if (sib.text or "").strip():        # comment/PI outside the root: reported, dropped on write
            units.append(Unit(f"pkg:{name}#o{i}", name, [(sib.text, lambda nv: None)]))

    n = 0
    text_fields = 0
    attr_fields = 0
    for el in root.iter():
        is_el = isinstance(el.tag, str)
        if is_el:
            attr_fields += sum(1 for k, v in el.attrib.items() if k != _XML_SPACE and v.strip())
        if is_el and el.tag.rsplit("}", 1)[-1] not in _CODE_ELEMS:
            for k, v in el.attrib.items():
                local = k.rsplit("}", 1)[-1]
                if (k != _XML_SPACE and len(v) >= 6 and not _HEX.fullmatch(v)
                        and local not in _CODE_ATTRS and not local.startswith("rsid")
                        and not (local == "id" and v.isdigit())):
                    units.append(Unit(f"pkg:{name}#{n}", name, [(v, _set_attr(el, k))]))
                    n += 1
        pieces = []
        if el.text and not (is_el and el in claimed):
            pieces.append((el.text, _set_text(el)))
        for c in el:
            if c.tail:
                pieces.append((c.tail, _set_tail(c)))
        if any(t.strip() for t, _ in pieces):
            units.append(Unit(f"pkg:{name}#{n}", name, pieces))
            n += 1
            text_fields += 1
    if name not in _EXEMPT_PARTS and not should_remove(name):
        known = _KNOWN_PARTS.fullmatch(name) or str(root.tag).startswith(_KNOWN_ROOT_NS)
        fields = text_fields if known else text_fields + attr_fields
        if fields > 1:
            warnings.add("part with several separate text/attribute fields outside paragraphs "
                         "(cannot be reliably mapped)")


def scan(data: bytes, names: dict | None = None) -> Scan:
    """Parse every XML part of the package. names: {part: {xpath: unit_id}} for stable paragraph ids."""
    names = names or {}
    units: list[Unit] = []
    warnings: set[str] = set()
    roots: dict = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        order = [n for n in zf.namelist() if not n.endswith("/")]
        for name in order:
            if not is_xml_part(name):
                if not should_remove(name):      # removed on redaction, so not a coverage gap
                    warnings.add("binary/embedded package parts present (not inspected)")
                continue
            blob = zf.read(name)
            root = roots[name] = parse_xml(blob)
            if name.startswith("word/") and _DRAWING.search(blob):
                warnings.add("images/drawings present (not scanned)")
            if name.startswith("ppt/"):
                if _PPT_UNREAD.search(blob):
                    warnings.add("images/media/embedded objects present (not scanned)")
                if name.startswith(_PPT_UNREAD_DIRS):
                    warnings.add("charts, SmartArt, ActiveX or embedded objects present (not scanned)")
                if name.endswith(".rels"):
                    for rel in root.iter(f"{{{_REL_NS}}}Relationship"):
                        if rel.get("TargetMode") == "External" and not rel.get("Type", "").endswith("/hyperlink"):
                            warnings.add("external (linked) objects present (not scanned)")
            if name == "[Content_Types].xml" and b"macroEnabled" in blob:
                warnings.add("macro-enabled content present (not scanned)")
            _scan_part(name, root, names, units, warnings)
    return Scan(units, sorted(warnings), roots, order)


def apply_spans(unit: Unit, spans: list[tuple[int, int, str]]) -> None:
    """Rewrite every piece a span touches: the placeholder goes into the first editable piece,
    the covered characters are removed from all of them. Raises if a span cannot be placed."""
    pos = 0
    bounds = []
    for text, setter in unit.pieces:
        bounds.append((pos, pos + len(text), text, setter))
        pos += len(text)
    placed: set[int] = set()
    for s0, e0, text, setter in bounds:
        if setter is None:
            continue
        out, cur, touched = [], s0, False
        for i, (s, e, label) in enumerate(spans):
            if e <= s0 or s >= e0:
                continue
            touched = True
            out.append(text[cur - s0:max(s, s0) - s0])
            if i not in placed:
                out.append(label)
                placed.add(i)
            cur = min(e, e0)
        if touched:
            out.append(text[cur - s0:])
            setter("".join(out))
    if len(placed) != len(spans):
        raise ValueError("a finding could not be mapped to editable text")


def _blank(x) -> bool:
    return x is None or x == ""


def _local(el) -> str:
    return etree.QName(el).localname


def _core_keep(c) -> bool:
    return (isinstance(c.tag, str) and _local(c) in _CORE_KEEP and len(c) == 0
            and _DATE.fullmatch(c.text or "") is not None)


def _app_keep(c) -> bool:
    rx = _APP_KEEP.get(_local(c)) if isinstance(c.tag, str) else None
    return rx is not None and len(c) == 0 and rx.fullmatch(c.text or "") is not None


def _meta_clean(root, keep, allowed_attr) -> bool:
    """True only if the tree holds nothing but validated fields, with no loose text or attributes."""
    if not _blank(root.text) or root.attrib:
        return False
    for c in root:
        if not keep(c) or not _blank(c.tail):
            return False
        for k, v in c.attrib.items():
            if allowed_attr.get(k) != v:
                return False
    return True


_CORE_ATTR = {_XSI_TYPE: "dcterms:W3CDTF"}


def _core_clean(root) -> bool:
    return _meta_clean(root, _core_keep, _CORE_ATTR)


def _app_clean(root) -> bool:
    return _meta_clean(root, _app_keep, {})


def _rebuild_meta(root, is_clean, keep) -> bool:
    if is_clean(root):
        return False
    root.text = None
    for k in list(root.attrib):
        del root.attrib[k]
    for c in list(root):
        if not keep(c):
            root.remove(c)          # removes its tail text with it
            continue
        c.tail = None
        for k in list(c.attrib):
            if _CORE_ATTR.get(k) != c.get(k) or is_clean is _app_clean:
                del c.attrib[k]
    return True


def _scrub(name: str, root) -> bool:
    changed = False
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr, new in ((f"{{{W}}}author", "Author"), (f"{{{W}}}initials", "")):
            if attr in el.attrib and el.get(attr) != new:
                el.set(attr, new)
                changed = True
    if name == "docProps/core.xml":
        changed |= _rebuild_meta(root, _core_clean, _core_keep)
    elif name == "docProps/app.xml":
        changed |= _rebuild_meta(root, _app_clean, _app_keep)
    elif name == "word/settings.xml":
        for el in list(root.iter(f"{{{W}}}docVars")):
            el.getparent().remove(el)
            changed = True
    return changed


def _drop_rels(name: str, root, removed: set[str]) -> bool:
    base = posixpath.dirname(posixpath.dirname(name))      # "word/_rels/x.rels" -> "word"
    changed = False
    for rel in list(root.iter(f"{{{_REL_NS}}}Relationship")):
        if rel.get("TargetMode") == "External":
            continue
        t = rel.get("Target", "")
        target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
        if target in removed:
            root.remove(rel)
            changed = True
    return changed


def _drop_overrides(root, removed: set[str]) -> bool:
    changed = False
    for o in list(root.iter(f"{{{_CT_NS}}}Override")):
        if o.get("PartName", "").lstrip("/") in removed:
            root.remove(o)
            changed = True
    return changed


def finalize_part(name: str, root, removed: set[str], touched: bool) -> bool:
    """Metadata scrub + relationship cleanup. Returns True if the part must be re-serialised."""
    changed = touched | _scrub(name, root)
    if name.endswith(".rels"):
        changed |= _drop_rels(name, root, removed)
    if name == "[Content_Types].xml":
        changed |= _drop_overrides(root, removed)
    return changed


def verify_scrubbed(names: list[str], read) -> None:
    """Re-read the saved package and confirm metadata removal. Raises ValueError otherwise."""
    if any(should_remove(n) for n in names):
        raise ValueError("package still contains removable metadata parts")
    for n in names:
        if not is_xml_part(n):
            continue
        root = parse_xml(read(n))
        for el in root.iter():
            if isinstance(el.tag, str):
                if el.get(f"{{{W}}}author") not in (None, "Author") or el.get(f"{{{W}}}initials"):
                    raise ValueError("reviewer identity remains")
                if el.tag == f"{{{W}}}docVars":
                    raise ValueError("docVars remain")
        if n == "docProps/core.xml" and not _core_clean(root):
            raise ValueError("core properties not clean")
        if n == "docProps/app.xml" and not _app_clean(root):
            raise ValueError("app properties not clean")
