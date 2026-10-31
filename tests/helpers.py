"""What the tests share: the template's page body, Hancom XML names, test images and readers of the result."""

import struct
import zlib

from hwpx import HwpxDocument

from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE


HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"  # Hancom paragraph XML
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"  # Hancom header XML


def _page_body() -> tuple[int, int, int, int]:
    """The template's 본문 left margin, body width, body height and text width (as convert.py computes them)."""
    template = HwpxDocument.open(TEMPLATE)
    style = template.styles.resolve("본문")
    margin = template.styles.paragraph_property(style.para_pr_id_ref).margin
    size, page = template.page.size(), template.page.margins()
    text_width = size.width - page.left - page.right - page.gutter
    body_width = text_width - int(margin.left) - int(margin.right)
    body_height = size.height - page.top - page.bottom - page.header - page.footer
    return int(margin.left), body_width, body_height, text_width


BODY_LEFT, BODY_WIDTH, BODY_HEIGHT, TEXT_WIDTH = _page_body()


def _text(element) -> str:
    """The text in *element* as Hancom shows it: a running number (hp:autoNum) as its cached value."""
    return "".join(
        e.get("num") if e.tag == f"{HP}autoNum" else "".join(e.itertext()) for e in element.iter(f"{HP}t", f"{HP}autoNum")
    )


def _png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    rows = (b"\x00" + b"\x00" * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _new_numbers(paragraph) -> list[str]:
    """The numTypes *paragraph* starts over (hp:newNum), each at 1."""
    news = list(paragraph.element.iter(f"{HP}newNum"))
    assert all(n.get("num") == "1" for n in news)
    return [n.get("numType") for n in news]


def _paragraph_info(doc, p):
    pp = doc.styles.paragraph_property(p.para_pr_id_ref)
    return p.text, pp.heading.type, int(pp.margin.left)
