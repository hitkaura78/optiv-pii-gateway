"""PDF structure leaks found in review: tag tree, thumbnails, link keys, marked content, vector text."""
import zlib

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from pii_pipeline import cli, detect, pdf_extract, pdf_redact
from pii_pipeline.models import PIIType
from tests.test_pdf import (  # noqa: F401  (fake_ocr is a fixture imported for use)
    EMAIL, PAGE_H, PAGE_W, doc_of, fake_ocr, native_pdf, pdf_text_and_streams, png_page, released, scanned_pdf,
)


def patch(path, fn):
    d = pymupdf.open(str(path))
    fn(d, d[0])
    d.saveIncr()
    d.close()


def live(d, xref):
    return {k for k in d.xref_get_keys(xref) if d.xref_get_key(xref, k)[0] != "null"}


def keys_of(path):
    d = pymupdf.open(str(path))
    cat = live(d, d.pdf_catalog())
    pages = [live(d, p.xref) for p in d]
    d.close()
    return cat, pages


def test_structure_tree_alt_text_is_removed(tmp_path):
    def setup(d, p):
        d.xref_set_key(d.pdf_catalog(), "StructTreeRoot",
                       f"<</Type/StructTreeRoot/K<</S/Figure/Alt({EMAIL})>>>>")
        d.xref_set_key(d.pdf_catalog(), "MarkInfo", "<</Marked true>>")
    src = native_pdf(tmp_path / "t.pdf", ["body text"], setup)
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    out = released(tmp_path / "o")[0]
    assert "jane.tester" not in pdf_text_and_streams(out)
    cat, _ = keys_of(out)
    assert not ({"StructTreeRoot", "MarkInfo"} & cat)


def test_page_thumbnail_is_removed(tmp_path):
    img = Image.new("L", (700, 80), 255)
    ImageDraw.Draw(img).text((5, 10), f"Email {EMAIL}", fill=0, font=ImageFont.load_default(size=40))

    def setup(d, p):
        x = d.get_new_xref()
        d.update_object(x, f"<</Type/XObject/Subtype/Image/Width 700/Height 80/ColorSpace/DeviceGray"
                           f"/BitsPerComponent 8/Filter/FlateDecode>>")
        d.update_stream(x, zlib.compress(img.tobytes()), compress=False)
        d.xref_set_key(p.xref, "Thumb", f"{x} 0 R")
    src = native_pdf(tmp_path / "th.pdf", ["body text"], setup)
    assert not any(u.unit_id.endswith("/ocr") for u in doc_of(src).units)      # the page draws no image
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    out = released(tmp_path / "o")[0]
    _, pages = keys_of(out)
    assert all("Thumb" not in k for k in pages)
    d = pymupdf.open(str(out))
    assert not any("/Subtype/Image" in d.xref_object(x, compressed=False).replace(" ", "")
                   for x in range(1, d.xref_length()))                          # the thumbnail image is gone


def test_link_annotation_extra_keys_are_stripped(tmp_path):
    def setup(d, p):
        p.insert_text((72, 100), "see link", fontsize=11)
        p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 120, 104), "uri": "https://example.org/x"})
    src = native_pdf(tmp_path / "l.pdf", [], setup)

    def add_keys(d, p):
        x = p.get_links()[0]["xref"]
        d.xref_set_key(x, "Contents", f"({EMAIL})")
        d.xref_set_key(x, "T", f"({EMAIL})")
    patch(src, add_keys)
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    out = released(tmp_path / "o")[0]
    assert "jane.tester" not in pdf_text_and_streams(out)
    d = pymupdf.open(str(out))
    assert [lk["uri"] for lk in d[0].get_links()] == ["https://example.org/x"]    # the real link survives


def test_launch_action_link_is_dropped(tmp_path):
    def setup(d, p):
        p.insert_text((72, 100), "see link", fontsize=11)
        p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 120, 104), "uri": "https://example.org/x"})
    src = native_pdf(tmp_path / "la.pdf", [], setup)
    patch(src, lambda d, p: d.xref_set_key(p.get_links()[0]["xref"], "A", f"<</S/Launch/F({EMAIL})>>"))
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) in (0, 1)
    for out in released(tmp_path / "o"):
        assert "jane.tester" not in pdf_text_and_streams(out)


def test_marked_content_actualtext_blocks_release(tmp_path):
    def setup(d, p):
        x = p.get_contents()[0]
        d.update_stream(x, b"/Span <</ActualText (" + EMAIL.encode() + b")>> BDC " + d.xref_stream(x) + b" EMC")
    src = native_pdf(tmp_path / "m.pdf", ["body text"], setup)
    assert not doc_of(src).complete
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 1
    assert released(tmp_path / "o") == []


def test_verify_saved_rejects_leftovers(tmp_path):
    def setup(d, p):
        d.xref_set_key(d.pdf_catalog(), "StructTreeRoot", f"<</Type/StructTreeRoot/K<</Alt({EMAIL})>>>>")
    bad = native_pdf(tmp_path / "bad.pdf", ["x"], setup)
    with pytest.raises(ValueError):
        pdf_redact.verify_saved(bad)
    good = native_pdf(tmp_path / "good.pdf", ["x"])
    pdf_redact.verify_saved(good)


def test_vector_paths_trigger_ocr_even_with_real_text(tmp_path, fake_ocr):
    def setup(d, p):
        for i in range(40):
            p.draw_rect(pymupdf.Rect(300 + i * 3, 400, 302 + i * 3, 420))
    src = native_pdf(tmp_path / "v.pdf", ["A normal paragraph of real text, long enough to count as a text layer."], setup)
    fake_ocr([("Plain", 100, 100, 40, 10)])
    ids = [u.unit_id for u in doc_of(src).units]
    assert "page:1/ocr" in ids


def test_single_compound_vector_path_triggers_ocr(tmp_path, fake_ocr):
    def setup(d, p):
        shape = p.new_shape()
        for i in range(40):                       # 40 segments, but ONE path (get_drawings() == 1)
            shape.draw_line(pymupdf.Point(300 + i * 3, 400), pymupdf.Point(300 + i * 3, 420))
        shape.finish(color=(0, 0, 0))
        shape.commit()
    src = native_pdf(tmp_path / "cp.pdf", ["A normal paragraph of real text, long enough to count as a text layer."], setup)
    d = pymupdf.open(str(src))
    drawings = d[0].get_drawings()
    assert len(drawings) == 1 and len(drawings[0]["items"]) >= 40       # the fixture really is one compound path
    fake_ocr([("Plain", 100, 100, 40, 10)])
    assert "page:1/ocr" in [u.unit_id for u in doc_of(src).units]


def test_ocr_redaction_removes_vector_art_under_the_box(tmp_path, fake_ocr):
    src, words = scanned_pdf(tmp_path / "s.pdf", [(f"Mail {EMAIL}", 60, 100), ("Plain words here", 60, 200)])
    text, l, t, w, h = words[0]

    def add_vector(path):
        d = pymupdf.open(str(path))
        k = 72 / 100
        d[0].draw_rect(pymupdf.Rect((l + 60) * k, t * k, (l + 80) * k, (t + h) * k), color=(0, 0, 0), fill=(0, 0, 0))
        d.saveIncr()
    add_vector(src)
    fake_ocr([("Mail", l, t, 30, h), (EMAIL, l + 40, t, w - 40, h), words[1]])
    assert any(f.pii_type is PIIType.EMAIL for f in detect.detect(doc_of(src)))
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    d = pymupdf.open(str(released(tmp_path / "o")[0]))
    k = 72 / 100
    hit = pymupdf.Rect((l + 40) * k, t * k, (l + w) * k, (t + h) * k)
    # the redaction's own white fill is a vector rectangle too; the black art we added must be gone
    assert not [dr for dr in d[0].get_drawings()
                if pymupdf.Rect(dr["rect"]).intersects(hit) and dr.get("fill") == (0.0, 0.0, 0.0)]


@pytest.mark.xfail(strict=True, reason="KNOWN LIMITATION: a word broken mid-token across lines is not rejoined")
def test_word_broken_mid_token_across_lines(tmp_path):
    src = native_pdf(tmp_path / "w.pdf", ["Mail jane.tes", "ter@example.com today"])
    assert cli.main([str(src), "--out", str(tmp_path / "o")]) == 0
    assert "jane.tes" not in pdf_text_and_streams(released(tmp_path / "o")[0])
