"""What the tests share: the template's page body, Hancom XML names, test images and readers of the result."""

import struct
import zipfile
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


def _template_with_header(tmp_path, name: str, change):
    """The bundled template saved as *name*, its header.xml as *change* (text to text) makes it."""
    path = tmp_path / name
    with zipfile.ZipFile(TEMPLATE) as src, zipfile.ZipFile(path, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "Contents/header.xml":
                data = change(data.decode("utf-8")).encode("utf-8")
            dst.writestr(item, data)
    return path


def _template_renaming(tmp_path, names: dict[str, str], name: str = "renamed.hwpx"):
    """The bundled template with its styles renamed: *names* maps each old name to its new one."""
    def change(header: str) -> str:
        for old, new in names.items():
            header = header.replace(f'name="{old}"', f'name="{new}"')
        return header

    return _template_with_header(tmp_path, name, change)


def _template_with_code_style(tmp_path):
    """The bundled template with its 표내부 style (한컴돋움 12pt, 160%) renamed 코드블록."""
    return _template_renaming(tmp_path, {"표내부": "코드블록"}, "code-style.hwpx")


def _template_without_bullet_styles(tmp_path):
    """The bundled template with its 글머리표 styles renamed, as a template that has none: lists then take
    python-hwpx's bullets in the 본문 style."""
    return _template_with_header(
        tmp_path, "no-bullet-styles.hwpx", lambda header: header.replace('name="글머리표 ', 'name="옛 글머리표 ')
    )
