"""Pictures: sizes, placement, other formats, and the placeholder for an image that cannot go in."""

import io
import shutil
import zipfile
from pathlib import Path

import pytest
from hwpx import HwpxDocument
from PIL import Image

from helpers import BODY_HEIGHT, BODY_LEFT, BODY_WIDTH, HP, _paragraph_info, _png, _text
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import MISSING_IMAGE, convert
from md2hwpx.source import unusable_images


def _jpeg(width: int, height: int) -> bytes:
    """JPEG header bytes up to the start-of-frame marker: enough to read the size."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + (11).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_convert_picture_sizes(tmp_path):
    images = {
        "mermaid-0123456789ab.png": _png(10, 5),  # diagram: body width
        "plantuml-0123456789ab.png": _png(10, 100),  # tall diagram: limited by the body height
        "small.png": _png(200, 100),  # natural size at 96 DPI
        "wide.png": _png(4000, 1000),  # wider than the body
        "tall.png": _png(100, 2000),  # taller than the body, with a caption
        "photo.jpg": _jpeg(400, 300),
        "anim.gif": b"GIF89a" + (300).to_bytes(2, "little") + (150).to_bytes(2, "little") + b"\x00" * 8,
    }
    for name, data in images.items():
        (tmp_path / name).write_bytes(data)
    md = tmp_path / "pictures.md"
    md.write_text(
        "".join(("[그림] 캡션\n" if name == "tall.png" else "") + f"![]({name})\n\n" for name in images),
        encoding="utf-8",
    )
    out = tmp_path / "pictures.hwpx"

    convert(md, TEMPLATE, out)

    # less a line of space above and below the picture
    body_height = BODY_HEIGHT - 2 * 1400
    doc = HwpxDocument.open(out)
    sizes = [(ref.width, ref.height) for ref in doc.media.picture_references()]
    assert sizes == [
        (BODY_WIDTH, BODY_WIDTH // 2),
        (round(body_height / 10), body_height),
        (200 * 75, 100 * 75),
        (BODY_WIDTH, round(BODY_WIDTH / 4)),
        (round((body_height - 2500) / 20), body_height - 2500),
        (400 * 75, 300 * 75),
        (300 * 75, 150 * 75),
    ]
    margins = [pic.find(f"{HP}outMargin").attrib for p in doc.paragraphs for pic in p.element.iter(f"{HP}pic")]
    assert margins == [{"left": "0", "right": "0", "top": "1400", "bottom": "1400"}] * len(images)


def test_convert_picture_size_attributes(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(100, 50))  # 7500 x 3750 at 96 DPI
    attributes = [
        "{width=50%}",  # of the body width
        '{width="80mm"}',  # larger than the image: grows
        "{height=40mm}",
        "{width=30mm height=30mm}",  # both: as asked
        "{width=200%}",  # wider than the body: scaled down
        "{height=50%}",  # of the body height, then scaled down to the body width
        "{width=300px .wide #a}",  # no such unit: its own size
    ]
    md = tmp_path / "sizes.md"
    md.write_text("".join(f"![](a.png){a} 뒤\n\n" for a in attributes), encoding="utf-8")
    out = tmp_path / "sizes.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)

    def mm(value):
        return round(value * 7200 / 25.4)

    sizes = [(ref.width, ref.height) for ref in doc.media.picture_references()]
    assert sizes == [
        (BODY_WIDTH // 2, round(BODY_WIDTH / 4)),
        (mm(80), round(mm(80) / 2)),
        (mm(40) * 2, mm(40)),
        (mm(30), mm(30)),
        (BODY_WIDTH, BODY_WIDTH // 2),
        (BODY_WIDTH, BODY_WIDTH // 2),
        (7500, 3750),
    ]
    # The attribute block is not left as text.
    assert [p.text for p in doc.paragraphs if "뒤" in p.text] == ["뒤"] * len(attributes)


def test_convert_reduced_picture_is_centred(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(100, 50))
    (tmp_path / "wide.png").write_bytes(_png(4000, 1000))
    md = tmp_path / "centre.md"
    md.write_text(
        "![](a.png){width=50%}\n\n"  # narrower than the body: centred
        "![](wide.png)\n\n"  # fills the body: nothing to centre
        "> ![](a.png){width=50%}\n",  # inside a quote, still narrower than what is left
        encoding="utf-8",
    )
    out = tmp_path / "centre.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    alignments = []
    for paragraph in doc.paragraphs:
        if list(paragraph.element.iter(f"{HP}pic")):
            alignments.append(doc.styles.paragraph_property(paragraph.para_pr_id_ref).align.horizontal)
    assert alignments == ["CENTER", "JUSTIFY", "CENTER"]
    # The pictures' own size is not touched by the alignment.
    assert [ref.width for ref in doc.media.picture_references()] == [
        BODY_WIDTH // 2,
        BODY_WIDTH,
        BODY_WIDTH // 2,
    ]


def test_convert_missing_image_is_a_placeholder(tmp_path):
    md = tmp_path / "missing.md"
    md.write_text("[그림] 없는 그림\n\n![](nope.png){width=50%}\n", encoding="utf-8")
    out = tmp_path / "missing.hwpx"

    convert(md, TEMPLATE, out)

    # The placeholder takes the image's place: its caption and size attributes still apply.
    with zipfile.ZipFile(out) as hwpx:
        images = [hwpx.read(n) for n in hwpx.namelist() if n.startswith("BinData/")]
    assert images == [MISSING_IMAGE.read_bytes()]
    doc = HwpxDocument.open(out)
    pic = next(e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic"))
    assert _text(pic.find(f"{HP}caption")) == "[그림 1] 없는 그림"
    assert int(pic.find(f"{HP}curSz").get("width")) == BODY_WIDTH // 2


def _image(width: int, height: int, image_format: str) -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(data, image_format)
    return data.getvalue()


def _embedded(out: Path) -> list[bytes]:
    with zipfile.ZipFile(out) as hwpx:
        return [hwpx.read(n) for n in sorted(hwpx.namelist()) if n.startswith("BinData/")]


def test_convert_other_image_formats_become_png(tmp_path):
    (tmp_path / "a.bmp").write_bytes(_image(40, 20, "BMP"))
    (tmp_path / "b.webp").write_bytes(_image(40, 20, "WEBP"))
    (tmp_path / "c.tiff").write_bytes(_image(40, 20, "TIFF"))
    (tmp_path / "png.jpg").write_bytes(_png(40, 20))  # named for one format, holding another
    md = tmp_path / "formats.md"
    md.write_text("![](a.bmp)\n\n![](b.webp)\n\n![](c.tiff)\n\n![](png.jpg)\n", encoding="utf-8")
    out = tmp_path / "formats.hwpx"

    assert unusable_images(md.read_text(encoding="utf-8"), tmp_path) == []
    convert(md, TEMPLATE, out)

    # Each goes in as a PNG of its own size, the original one as it is.
    images = _embedded(out)
    assert len(images) == 4 and all(i.startswith(b"\x89PNG") for i in images)
    assert _png(40, 20) in images
    assert all(Image.open(io.BytesIO(i)).size == (40, 20) for i in images)
    pics = [e for p in HwpxDocument.open(out).paragraphs for e in p.element.iter(f"{HP}pic")]
    assert [(int(p.find(f"{HP}curSz").get("width")), int(p.find(f"{HP}curSz").get("height"))) for p in pics] == [
        (40 * 75, 20 * 75)
    ] * 4  # 96 DPI


def test_convert_unreadable_image_is_a_placeholder(tmp_path):
    (tmp_path / "broken.png").write_bytes(b"not an image")
    md = tmp_path / "broken.md"
    md.write_text("본문\n\n![](broken.png)\n\n![](gone.png)\n", encoding="utf-8")
    out = tmp_path / "broken.hwpx"

    assert unusable_images(md.read_text(encoding="utf-8"), tmp_path) == [
        (3, tmp_path / "broken.png", "이미지 형식을 읽을 수 없어"),
        (5, tmp_path / "gone.png", "이미지 파일이 없어"),
    ]
    convert(md, TEMPLATE, out)

    assert _embedded(out) == [MISSING_IMAGE.read_bytes()] * 2


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="red"/></svg>'


@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="rsvg-convert not installed")
def test_convert_svg_becomes_png(tmp_path):
    (tmp_path / "a.svg").write_bytes(SVG)
    md = tmp_path / "svg.md"
    md.write_text("![](a.svg)\n", encoding="utf-8")
    out = tmp_path / "svg.hwpx"

    convert(md, TEMPLATE, out)

    [image] = _embedded(out)
    assert Image.open(io.BytesIO(image)).size == (40, 20)


def test_convert_svg_without_rsvg_convert_is_a_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no rsvg-convert on it
    (tmp_path / "a.svg").write_bytes(SVG)
    md = tmp_path / "svg.md"
    md.write_text("![](a.svg)\n", encoding="utf-8")
    out = tmp_path / "svg.hwpx"

    assert unusable_images(md.read_text(encoding="utf-8"), tmp_path) == [
        (1, tmp_path / "a.svg", "SVG 를 변환할 rsvg-convert 가 없어"),
    ]
    convert(md, TEMPLATE, out)

    assert _embedded(out) == [MISSING_IMAGE.read_bytes()]


def test_convert_image_inside_text(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(2, 1))
    md = tmp_path / "inline.md"
    md.write_text("앞 문장 ![](a.png) 뒤 문장\n\n[그림] 캡션\n![](a.png)\n", encoding="utf-8")
    out = tmp_path / "inline.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    assert [p.text for p in doc.paragraphs[1:]] == ["앞 문장", "", "뒤 문장", ""]
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    assert len(pics) == 2
    assert pics[0].find(f"{HP}caption") is None
    assert _text(pics[1].find(f"{HP}caption")) == "[그림 1] 캡션"


def test_convert_pictures_in_list_items(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(2, 1))
    md = tmp_path / "list-pictures.md"
    md.write_text("1. 단계\n\n   ![](a.png)\n2. ![](a.png)\n", encoding="utf-8")
    out = tmp_path / "list-pictures.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    assert [_paragraph_info(doc, p) for p in doc.paragraphs[1:]] == [
        ("단계", "NUMBER", BODY_LEFT),
        ("", "NONE", BODY_LEFT + 1000),  # picture under the item
        ("", "NUMBER", BODY_LEFT),  # an item holding only a picture keeps its number ...
        ("", "NONE", BODY_LEFT + 1000),  # ... and the picture follows
    ]
    assert len(doc.media.picture_references()) == 2
