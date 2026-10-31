import io
import shutil
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from hwpx import HwpxDocument
from hwpx.form_fit.measure import estimate_text_width, measure, resolve_slot_metrics
from hwpx.layout import lint_layout
from PIL import Image

from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import MISSING_IMAGE, convert, paragraph_styles
from md2hwpx.source import ConversionError, caption_numbering, unknown_styles, unusable_images

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


def _jpeg(width: int, height: int) -> bytes:
    """JPEG header bytes up to the start-of-frame marker: enough to read the size."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + (11).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_convert(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "doc.md"
    md.write_text(
        "# 제목\n\n첫 줄\n둘째 줄\n\n## 소제목\n\n![](a.png)\n\n```\ncode # x\n```\n",
        encoding="utf-8",
    )
    out = tmp_path / "doc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    styles = {st.id: st for st in doc.styles.values()}
    # paragraphs[0] is the template's own (empty) paragraph.
    paragraphs = doc.paragraphs[1:]
    texts = [(p.text, styles[int(p.style_id_ref)].name) for p in paragraphs]
    assert texts == [
        ("제목", "개요 1"),
        ("첫 줄 둘째 줄", "본문"),
        ("", "본문"),  # before the sub-heading, not before the level 1 one
        ("소제목", "개요 2"),
        ("", "바탕글"),  # picture: laid out as a character, so no 본문 first-line indent
        ("", "본문"),  # code block table anchor
    ]
    # Each paragraph uses its style's own paragraph shape, not the previous heading's. A heading is the
    # exception: add_heading makes an OUTLINE shape of its own, at the level of the 개요 style it is given.
    # A picture narrower than the body is the other: it gets the plain style's shape aligned to centre.
    for p in paragraphs:
        style = styles[int(p.style_id_ref)]
        if style.name.startswith("개요 "):
            own = doc.styles.paragraph_property(p.para_pr_id_ref).heading
            assert (own.type, own.level) == ("OUTLINE", int(style.name.split()[1]) - 1)
        elif style.name == "바탕글":
            own = doc.styles.paragraph_property(p.para_pr_id_ref)
            assert own.align.horizontal == "CENTER"
        else:
            assert int(p.para_pr_id_ref) == style.para_pr_id_ref
    assert len(doc.media.picture_references()) == 1


def test_convert_code_block(tmp_path):
    md = tmp_path / "code.md"
    md.write_text('```python\ndef f():\n    """doc\n    string"""\n\n    return 1\n```\n', encoding="utf-8")
    out = tmp_path / "code.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    table = doc.tables.all[0]
    assert (table.row_count, table.column_count) == (1, 1)
    # A line of space above and below, none at the sides, as a table.
    margin = table.element.find(f"{HP}outMargin").attrib
    assert margin == {"left": "0", "right": "0", "top": "1400", "bottom": "1400"}
    paragraphs = table.cell(0, 0).paragraphs
    assert [p.text for p in paragraphs] == ["def f():", '    """doc', '    string"""', "", "    return 1"]
    colors = {}
    for p in paragraphs:
        assert doc.styles.paragraph_property(p.para_pr_id_ref).line_spacing.value == 140
        for run in p.runs:
            char_pr_id = run.element.get("charPrIDRef")
            assert doc.styles.char_property(char_pr_id).attributes["height"] == "900"
            assert doc.styles.font_face(char_pr_id, "HANGUL") == "D2Coding"
            colors[run.text] = doc.styles.char_property(char_pr_id)
    # Pygments "default" style: keyword green bold, function name blue, string red italic.
    assert colors["def"].attributes["textColor"] == "#008000" and "bold" in colors["def"].child_attributes
    assert colors["f"].attributes["textColor"] == "#0000FF"
    assert colors['    string"""'].attributes["textColor"] == "#BA2121"
    assert "italic" in colors['    string"""'].child_attributes


def test_convert_code_block_without_language(tmp_path):
    md = tmp_path / "plain.md"
    md.write_text("```\ndef f():\n```\n\n```nosuchlang\nx\n```\n", encoding="utf-8")
    out = tmp_path / "plain.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    runs = [run for table in doc.tables.all for p in table.cell(0, 0).paragraphs for run in p.runs]
    assert [run.text for run in runs] == ["def f():", "x"]
    assert all(doc.styles.char_property(run.element.get("charPrIDRef")).attributes["textColor"] == "#000000" for run in runs)


def test_convert_console_code_block(tmp_path):
    # A console fence: the shell session lexers read "(gdb) " as a virtualenv prompt, a token the style
    # has no entry for, and leave the last line without a "\n" unhighlighted.
    md = tmp_path / "console.md"
    md.write_text(
        "```console\nASR-9010:/disk0:/var/tmp$ pidin | grep locald\n221309   1 locald_DSC\n(gdb) x/4xi 0x42371e4\n(gdb) c\n```\n",
        encoding="utf-8",
    )
    out = tmp_path / "console.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    paragraphs = doc.tables.all[0].cell(0, 0).paragraphs
    assert [p.text for p in paragraphs] == [
        "ASR-9010:/disk0:/var/tmp$ pidin | grep locald",
        "221309   1 locald_DSC",
        "(gdb) x/4xi 0x42371e4",
        "(gdb) c",
    ]
    # The prompt takes the color of its Generic.Prompt parent; the last line is highlighted too.
    colors = []
    for p in paragraphs:
        for run in p.runs:
            if run.text in ("(gdb)", "(gdb) c"):
                colors.append(doc.styles.char_property(run.element.get("charPrIDRef")).attributes["textColor"])
    assert colors[:2] == ["#000080"] * 2


def test_convert_table(tmp_path):
    md = tmp_path / "table.md"
    md.write_text(
        "| 왼쪽 | 가운데 | 오른쪽 | 기본 |\n|:--|:-:|--:|---|\n| a | b \\| c | 3 |\n| d |\n\n뒤 문단\n",
        encoding="utf-8",
    )
    out = tmp_path / "table.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    table = doc.tables.all[0]
    assert (table.row_count, table.column_count) == (3, 4)
    texts = [[table.cell(r, c).text for c in range(4)] for r in range(3)]
    assert texts == [["왼쪽", "가운데", "오른쪽", "기본"], ["a", "b | c", "3", ""], ["d", "", "", ""]]

    def bold(cell):
        return "bold" in doc.styles.char_property(cell.paragraphs[0].char_pr_id_ref).child_attributes

    def align(cell):
        return doc.styles.paragraph_property(cell.paragraphs[0].para_pr_id_ref).align.horizontal

    def fill(cell):
        return doc.styles.border_fill_info(cell.element.get("borderFillIDRef")).fill

    header = [table.cell(0, c) for c in range(4)]
    body = [table.cell(1, c) for c in range(4)]
    assert all(bold(cell) and fill(cell) == "#D9D9D9" for cell in header)
    assert not any(bold(cell) or fill(cell) for cell in body)

    def borders(cell):
        info = doc.styles.border_fill_info(cell.element.get("borderFillIDRef"))
        return info.left.type, info.right.type, info.top.type, info.bottom.type

    # The shading keeps the table's border: a fill matched on colour alone would drop all four sides.
    assert all(borders(cell) == ("SOLID",) * 4 for cell in header + body)
    # A line of space above and below, none at the sides.
    margin = table.element.find(f"{HP}outMargin").attrib
    assert margin == {"left": "0", "right": "0", "top": "1400", "bottom": "1400"}
    # The header row repeats on each page the table runs on to.
    assert table.element.get("repeatHeader") == "1"
    assert [cell.element.get("header") for cell in header + body] == ["1"] * 4 + ["0"] * 4
    assert [align(cell) for cell in body[:3]] == ["LEFT", "CENTER", "RIGHT"]
    # 바탕글 with 휴먼명조, so the 본문 first-line indent does not push the text in from the left edge.
    plain = doc.styles.resolve("바탕글")
    for cell in header + body:
        paragraph = cell.paragraphs[0]
        assert int(paragraph.style_id_ref) == plain.id
        assert int(doc.styles.paragraph_property(paragraph.para_pr_id_ref).margin.intent) == 0
        assert doc.styles.font_face(paragraph.char_pr_id_ref, "HANGUL") == "휴먼명조"
    # Cell padding, as Hancom's own tables have it: 1.8 mm at the sides, 0.5 mm above and below. The cells
    # leave hasMargin off, so Hancom takes the table's inMargin.
    padding = table.element.find(f"{HP}inMargin").attrib
    assert padding == {"left": "510", "right": "510", "top": "141", "bottom": "141"}
    for cell in header + body:
        assert cell.element.get("hasMargin") != "1"
        # The stored row height holds the padding plus one line of text (1800), or the padding would have
        # no room to show in.
        assert cell.height == 2082
    assert table.element.find(f"{HP}sz").get("height") == str(2082 * 3)
    assert doc.paragraphs[-1].text == "뒤 문단"


def test_convert_tables_flow_across_pages(tmp_path):
    # Taller than a page only once filled: a long code block, and a few rows of long text.
    code = "".join(f"x = {i}\n" for i in range(90))
    rows = "".join(f"| {i} | {'긴 내용 ' * 60} |\n" for i in range(10))
    md = tmp_path / "long.md"
    md.write_text(f"```python\n{code}```\n\n| a | b |\n|---|---|\n{rows}\n- 항목\n\n  | c |\n  |---|\n", encoding="utf-8")
    out = tmp_path / "long.hwpx"

    convert(md, TEMPLATE, out)

    tables = HwpxDocument.open(out).tables.all
    assert len(tables) == 3
    for table in tables:
        assert not table.treat_as_char
        # From the paragraph, so a table in a list item keeps its indent.
        assert table.element.find(f"{HP}pos").get("horzRelTo") == "PARA"
    assert not [f for f in lint_layout(out.read_bytes()).findings if f.code == "TABLE_TALLER_THAN_PAGE"]


def test_convert_caption_numbers_restart_in_each_chapter(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "chapters.md"
    md.write_text(
        "# 1장\n\n[표] 첫 표\n| a |\n|---|\n| 1 |\n\n[그림] 첫 그림\n![](a.png)\n\n"
        "## 1.1\n\n[표] 둘째 표\n| b |\n|---|\n| 2 |\n\n"
        "# 2장\n\n[표] 장2 표1\n| c |\n|---|\n| 3 |\n\n[그림] 장2 그림1\n![](a.png)\n",
        encoding="utf-8",
    )
    out = tmp_path / "chapters.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # The number is the chapter (level 1 heading) and the object's own count within it.
    assert [_text(t.caption.element) for t in doc.tables.all] == ["[표 1-1] 첫 표", "[표 1-2] 둘째 표", "[표 2-1] 장2 표1"]
    pictures = [_text(pic.find(f"{HP}caption")) for p in doc.paragraphs for pic in p.element.iter(f"{HP}pic")]
    assert pictures == ["[그림 1-1] 첫 그림", "[그림 2-1] 장2 그림1"]


def test_convert_captions(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "cap.md"
    md.write_text(
        "[표] 표 캡션\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "[그림] 그림 캡션\n\n![](a.png)\n\n"
        "[표] 표가 뒤따르지 않음\n\n문단\n\n"
        "[표] 둘째 표\n| c |\n|---|\n\n"
        "[표] 코드 캡션\n\n```\nx = 1\n```\n",
        encoding="utf-8",
    )
    out = tmp_path / "cap.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    caption = doc.tables.all[0].caption
    # No level 1 heading here, so nothing is in a chapter and the numbers stay plain.
    assert (caption.side, _text(caption.element)) == ("TOP", "[표 1] 표 캡션")
    assert _text(doc.tables.all[1].caption.element) == "[표 2] 둘째 표"
    code_caption = doc.tables.all[2].caption
    assert (code_caption.side, _text(code_caption.element)) == ("TOP", "[표 3] 코드 캡션")
    pic = next(e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic"))
    pic_caption = pic.find(f"{HP}caption")
    assert pic_caption.get("side") == "BOTTOM"
    assert _text(pic_caption) == "[그림 1] 그림 캡션"
    assert [p.text for p in doc.paragraphs if p.text] == ["[표] 표가 뒤따르지 않음", "문단"]

    style = doc.styles.resolve("표그림 제목")
    caption_paragraphs = [p for t in doc.tables.all for p in t.caption.paragraphs]
    for p in caption_paragraphs:
        assert (int(p.style_id_ref), int(p.para_pr_id_ref), int(p.char_pr_id_ref)) == (
            style.id, style.para_pr_id_ref, style.char_pr_id_ref
        )
    pic_p = pic_caption.find(f".//{HP}p")
    assert (pic_p.get("styleIDRef"), pic_p.get("paraPrIDRef")) == (str(style.id), str(style.para_pr_id_ref))
    assert pic_p.find(f"{HP}run").get("charPrIDRef") == str(style.char_pr_id_ref)


def test_convert_alt_text_captions(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "alt.md"
    md.write_text(
        "![대체 텍스트 제목](a.png)\n\n"
        "[그림] 줄로 쓴 제목\n![대체 텍스트](a.png)\n\n"  # the "[그림]" line comes first
        "![](a.png)\n\n"  # no alt text: no caption, not counted
        "![ *기울임* 제목 ](a.png){width=50%}\n\n"
        "# 헤딩 ![헤딩 안 그림](a.png)\n",  # alt text in a heading stays its text
        encoding="utf-8",
    )
    out = tmp_path / "alt.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    captions = [pic.find(f"{HP}caption") for pic in pics]
    assert [c is not None and _text(c) for c in captions] == [
        "[그림 1] 대체 텍스트 제목",
        "[그림 2] 줄로 쓴 제목",
        False,
        "[그림 3] 기울임 제목",
    ]
    assert captions[0].get("side") == "BOTTOM"
    italic = [
        run.get("charPrIDRef") for run in captions[3].iter(f"{HP}run") if "".join(run.itertext()) == "기울임"
    ]
    assert italic and "italic" in doc.styles.char_property(italic[0]).child_attributes
    assert "헤딩 헤딩 안 그림" in [p.text for p in doc.paragraphs]


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


def test_convert_inline_code(tmp_path):
    md = tmp_path / "inline.md"
    md.write_text(
        "# `convert` 함수\n\n"
        "`python-hwpx` 로 열고 ``a `b` c`` 를 쓴다. 닫히지 않은 ` 는 그대로.\n\n"
        "[표] `md2hwpx` 표\n\n| `헤더` | 값 |\n|---|---|\n| `셀` | 1 |\n",
        encoding="utf-8",
    )
    out = tmp_path / "inline.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)

    def runs(paragraph):
        return [
            (run.text, doc.styles.font_face(run.element.get("charPrIDRef")) == "D2Coding")
            for run in paragraph.runs
            if run.text  # not a control's own run, such as a heading's number restart
        ]

    heading, body = doc.paragraphs[1:3]
    assert runs(heading) == [("convert", True), (" 함수", False)]
    assert runs(body) == [
        ("python-hwpx", True),
        (" 로 열고 ", False),
        ("a `b` c", True),
        (" 를 쓴다. 닫히지 않은 ` 는 그대로.", False),
    ]
    table = doc.tables.all[0]
    # The number is Hancom's own (hp:autoNum), a control in the first run rather than its text.
    assert runs(table.caption.paragraphs[0]) == [("[표 1-] ", False), ("md2hwpx", True), (" 표", False)]
    header_run = table.cell(0, 0).paragraphs[0].runs[0]
    assert runs(table.cell(0, 0).paragraphs[0]) == [("헤더", True)]
    assert "bold" in doc.styles.char_property(header_run.element.get("charPrIDRef")).child_attributes
    assert runs(table.cell(1, 0).paragraphs[0]) == [("셀", True)]
    # Heading keeps its style and outline level.
    assert int(heading.style_id_ref) == doc.styles.resolve("개요 1").id


def test_convert_table_column_widths(tmp_path):
    md = tmp_path / "widths.md"
    md.write_text(
        "| 번호 | 설명 | `code` |\n|---|---|---|\n"
        "| 1 | 열 너비가 내용 길이에 비례하는지 확인하는 긴 설명 | abcdefgh |\n",
        encoding="utf-8",
    )
    out = tmp_path / "widths.hwpx"

    convert(md, TEMPLATE, out)

    table = HwpxDocument.open(out).tables.all[0]
    widths = [table.cell(0, c).width for c in range(3)]
    # Every column carries its longest cell's text and its own padding, so no cell has to wrap: a width of
    # 1 unit per character would have been too narrow for the short columns, whose padding is 1020 of it.
    assert sum(widths) == TEXT_WIDTH
    assert widths[0] < widths[2] < widths[1]
    for column, text in enumerate(("번호", "code")):
        assert widths[column] - 1020 >= round(estimate_text_width(text, 10))
    assert [table.cell(1, c).width for c in range(3)] == widths


def test_convert_table_row_heights(tmp_path):
    md = tmp_path / "rows.md"
    # A cell taller than one line, and a code block of several lines: each must declare the height it
    # needs, or the table takes more room on the page than it says and pushes the next block away.
    md.write_text(
        "| 짧음 | 긴 설명 |\n|---|---|\n"
        "| a | 이 셀은 좁은 열이라 여러 줄로 접히는 아주 긴 문장을 담고 있습니다. 계속 이어집니다. |\n\n"
        "```py\n" + "".join(f"x{i} = {i}\n" for i in range(12)) + "```\n",
        encoding="utf-8",
    )
    out = tmp_path / "rows.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    table = doc.tables.all[0]
    # The two cells of the row share the row's height, which holds the taller one's wrapped lines.
    assert table.cell(0, 0).height == table.cell(0, 1).height
    wrapped = table.cell(1, 1)
    lines = measure(wrapped.text, resolve_slot_metrics(wrapped, doc)).lines
    assert lines > 1
    assert wrapped.height >= lines * 1800 + 141 * 2
    assert table.cell(1, 0).height == wrapped.height  # the short cell takes its row's height too
    assert table.element.find(f"{HP}sz").get("height") == str(
        table.cell(0, 0).height + table.cell(1, 0).height
    )

    # The code block is 12 lines at 1260 each (9pt at 140%), not the one line of a bare table.
    code = doc.tables.all[1].cell(0, 0)
    assert len(code.paragraphs) == 12
    assert code.height == 12 * 1260 + 141 * 2
    assert doc.tables.all[1].element.find(f"{HP}sz").get("height") == str(code.height)
    # One line of code is one code line high, not one line of the cells' 바탕글.
    md.write_text("```\none\n```\n", encoding="utf-8")
    convert(md, TEMPLATE, out)
    assert HwpxDocument.open(out).tables.all[0].cell(0, 0).height == 1260 + 141 * 2


def test_convert_lists(tmp_path):
    md = tmp_path / "list.md"
    md.write_text(
        "- 첫째 `code`\n  이어지는 줄\n- 둘째\n  - 하위\n    - 하위의 하위\n      - 4단계\n\n"
        "3. 셋부터\n4. 넷\n   1. 하위 번호\n5. 다섯\n\n"
        "문단\n2. 문단에 이어짐\n\n"
        "1. 새 목록\n",
        encoding="utf-8",
    )
    out = tmp_path / "list.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    body = doc.styles.resolve("본문")

    def info(p):
        pp = doc.styles.paragraph_property(p.para_pr_id_ref)
        return p.text, pp.heading.type, pp.heading.level + 1, int(pp.margin.left)

    paragraphs = doc.paragraphs[1:]
    assert [info(p) for p in paragraphs] == [
        ("첫째 code 이어지는 줄", "BULLET", 1, BODY_LEFT),
        ("둘째", "BULLET", 1, BODY_LEFT),
        ("하위", "BULLET", 2, BODY_LEFT + 1000),
        ("하위의 하위", "BULLET", 3, BODY_LEFT + 2000),
        ("4단계", "BULLET", 3, BODY_LEFT + 2000),  # deeper than 3 stays at level 3
        ("셋부터", "NUMBER", 1, BODY_LEFT),
        ("넷", "NUMBER", 1, BODY_LEFT),
        ("하위 번호", "NUMBER", 2, BODY_LEFT + 1000),
        ("다섯", "NUMBER", 1, BODY_LEFT),
        ("문단 2. 문단에 이어짐", "NONE", 1, BODY_LEFT),  # only "1." can interrupt a paragraph
        ("새 목록", "NUMBER", 1, BODY_LEFT),
    ]
    assert all(int(p.style_id_ref) == body.id for p in paragraphs)
    numbering = [doc.styles.paragraph_property(p.para_pr_id_ref).heading.id_ref for p in paragraphs]
    # One numbering for the whole "3." list, sub-level included; the later list starts a new one.
    assert len(set(numbering[5:9])) == 1
    assert numbering[10] != numbering[5]
    header = doc.oxml.headers[0].element
    first_list = header.find(f".//{HH}numbering[@id='{numbering[5]}']")
    assert first_list.find(f"{HH}paraHead[@level='1']").get("start") == "3"


def test_convert_inline_formatting(tmp_path):
    md = tmp_path / "format.md"
    md.write_text(
        "**굵게** *기울임* ~~취소~~ __굵게2__ _기울임2_ **a *b* c** ***둘다***\n\n"
        "file_name_x 와 \\*리터럴\\* 와 닫히지 않은 ** 그대로\n\n"
        "[링크 **굵게**](https://example.com) 끝\n\n"
        "| **머리** | 값 |\n|---|---|\n| *셀* | 1 |\n",
        encoding="utf-8",
    )
    out = tmp_path / "format.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)

    def marks(run):
        char_pr = doc.styles.char_property(run.element.get("charPrIDRef"))
        found = {name for name in ("bold", "italic") if name in char_pr.child_attributes}
        if char_pr.child_attributes["strikeout"]["shape"] != "NONE":
            found.add("strike")
        if char_pr.attributes["textColor"] == "#0000FF" and char_pr.child_attributes["underline"]["type"] != "NONE":
            found.add("link")
        return run.text, found

    def texts(paragraph):
        return [marks(run) for run in paragraph.runs if run.text]

    first, second, third = doc.paragraphs[1:4]
    assert texts(first) == [
        ("굵게", {"bold"}), (" ", set()), ("기울임", {"italic"}), (" ", set()), ("취소", {"strike"}),
        (" ", set()), ("굵게2", {"bold"}), (" ", set()), ("기울임2", {"italic"}), (" ", set()),
        ("a ", {"bold"}), ("b", {"bold", "italic"}), (" c", {"bold"}), (" ", set()), ("둘다", {"bold", "italic"}),
    ]
    assert texts(second) == [("file_name_x 와 *리터럴* 와 닫히지 않은 ** 그대로", set())]
    assert texts(third) == [("링크 ", {"link"}), ("굵게", {"bold", "link"}), (" 끝", set())]
    # One hyperlink field per run of the link text, all to the same URL.
    assert [link["url"] for link in third.hyperlinks] == ["https://example.com"] * 2

    table = doc.tables.all[0]
    assert texts(table.cell(0, 0).paragraphs[0]) == [("머리", {"bold"})]
    assert texts(table.cell(1, 0).paragraphs[0]) == [("셀", {"italic"})]


def test_convert_skips_front_matter(tmp_path):
    cases = {
        "yaml.md": "---\ntitle: 제목\ntags: [a, b]\n---\n# 본문 제목\n",
        "dots.md": "---\ntitle: x\n...\n# 본문 제목\n",
        "bom.md": "﻿---\ntitle: x\n---\n# 본문 제목\n",
        "empty.md": "---\n---\n# 본문 제목\n",
    }
    for name, text in cases.items():
        md = tmp_path / name
        md.write_text(text, encoding="utf-8")
        out = tmp_path / f"{name}.hwpx"
        convert(md, TEMPLATE, out)
        assert [p.text for p in HwpxDocument.open(out).paragraphs[1:]] == ["본문 제목"], name


def test_convert_keeps_dashes_that_are_not_front_matter(tmp_path):
    md = tmp_path / "doc.md"
    # Not at the very start, and an unclosed block: both stay content.
    md.write_text("본문\n\n---\ntitle: x\n---\n", encoding="utf-8")
    unclosed = tmp_path / "unclosed.md"
    unclosed.write_text("---\ntitle: x\n", encoding="utf-8")

    convert(md, TEMPLATE, tmp_path / "doc.hwpx")
    convert(unclosed, TEMPLATE, tmp_path / "unclosed.hwpx")

    # Not removed as front matter: after a blank line "---" is a thematic break (an empty paragraph),
    # right under a line it underlines a level 2 heading (CommonMark setext heading).
    doc = HwpxDocument.open(tmp_path / "doc.hwpx")
    assert [p.text for p in doc.paragraphs[1:]] == ["본문", "", "", "title: x"]
    assert int(doc.paragraphs[4].style_id_ref) == doc.styles.resolve("개요 2").id
    assert [p.text for p in HwpxDocument.open(tmp_path / "unclosed.hwpx").paragraphs[1:]] == ["", "title: x"]



def test_convert_thematic_break(tmp_path):
    md = tmp_path / "rule.md"
    md.write_text("앞\n\n---\n\n***\n- - -\n___\n\n뒤\n\n- 항목\n---\n", encoding="utf-8")
    out = tmp_path / "rule.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    paragraphs = doc.paragraphs[1:]
    assert [p.text for p in paragraphs] == ["앞", "", "", "", "", "뒤", "항목", ""]
    for p in [paragraphs[i] for i in (1, 2, 3, 4, 7)]:
        border = doc.styles.paragraph_property(p.para_pr_id_ref).border
        info = doc.styles.border_fill_info(border.border_fill_id_ref)
        assert (info.bottom.type, info.top.type, info.left.type) == ("SOLID", "NONE", "NONE")
    assert doc.styles.paragraph_property(paragraphs[6].para_pr_id_ref).heading.type == "BULLET"


def test_convert_block_quote(tmp_path):
    md = tmp_path / "quote.md"
    md.write_text(
        "> 첫 줄\n> **둘째** 줄\n>\n> 새 문단\n>> 중첩\n\n문단\n",
        encoding="utf-8",
    )
    out = tmp_path / "quote.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    body = doc.styles.resolve("본문")

    def info(p):
        pp = doc.styles.paragraph_property(p.para_pr_id_ref)
        bar = doc.styles.border_fill_info(pp.border.border_fill_id_ref)
        return p.text, int(pp.margin.left), bar.left.type, pp.border.connect

    paragraphs = doc.paragraphs[1:]
    assert [info(p) for p in paragraphs[:3]] == [
        ("첫 줄 둘째 줄", BODY_LEFT + 1000, "SOLID", True),
        ("새 문단", BODY_LEFT + 1000, "SOLID", True),
        ("중첩", BODY_LEFT + 2000, "SOLID", True),
    ]
    assert all(int(p.style_id_ref) == body.id for p in paragraphs)
    bold = [r.text for r in paragraphs[0].runs if "bold" in doc.styles.char_property(r.element.get("charPrIDRef")).child_attributes]
    assert bold == ["둘째"]
    assert info(paragraphs[3])[:2] == ("문단", BODY_LEFT)


def test_convert_hard_line_breaks(tmp_path):
    md = tmp_path / "breaks.md"
    md.write_text(
        "공백 두 칸  \n백슬래시\\\n**굵게  \n이어짐** 끝\n\n- 항목  \n  둘째 줄\n\n> 인용\\\n> 둘째\n",
        encoding="utf-8",
    )
    out = tmp_path / "breaks.hwpx"

    convert(md, TEMPLATE, out)
    paragraphs = HwpxDocument.open(out).paragraphs[1:]

    def lines(p):
        # hp:lineBreak inside hp:t marks a line break; text() reads it as "\n".
        return sum(1 for _ in p.element.iter(f"{HP}lineBreak")) + 1, p.text.replace("\n", "|")

    assert lines(paragraphs[0]) == (4, "공백 두 칸|백슬래시|굵게|이어짐 끝")
    assert lines(paragraphs[1]) == (2, "항목|둘째 줄")
    assert lines(paragraphs[2]) == (2, "인용|둘째")


def test_convert_footnotes(tmp_path):
    md = tmp_path / "notes.md"
    md.write_text(
        "본문[^a] 중간 **굵게[^b]** 끝[^없음].\n\n"
        "[^a]: 첫 각주 **서식은** 글자로\n"
        "    이어지는 줄\n"
        "[^b]: 둘째 각주\n\n"
        "| 셀[^a] |\n|---|\n| 1 |\n\n"
        "```\n[^a]: 코드 안은 그대로\n```\n",
        encoding="utf-8",
    )
    out = tmp_path / "notes.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)

    def sequence(p):
        """Runs' text and footnotes (as their text in braces) in document order."""
        items = []
        for run in p.element.findall(f"{HP}run"):
            for child in run:
                if child.tag == f"{HP}t" and child.text:
                    items.append(child.text)
                elif child.tag == f"{HP}ctrl" and child.find(f"{HP}footNote") is not None:
                    note = child.find(f"{HP}footNote")
                    items.append("{" + "".join(note.find(f".//{HP}subList").itertext()) + "}")
        return items

    first = doc.paragraphs[1]
    assert sequence(first) == [
        "본문", "{첫 각주 서식은 글자로 이어지는 줄}", " 중간 ", "굵게", "{둘째 각주}", " 끝[^없음].",
    ]
    cell = doc.tables.all[0].cell(0, 0).paragraphs[0]
    assert sequence(cell) == ["셀", "{첫 각주 서식은 글자로 이어지는 줄}"]
    code = doc.tables.all[1].cell(0, 0).paragraphs[0]
    assert code.text == "[^a]: 코드 안은 그대로"
    assert not any("[^b]:" in p.text for p in doc.paragraphs)


def test_convert_blank_page(tmp_path):
    md = tmp_path / "blank.md"
    md.write_text("본문\n\n[여백면] 이 면은 여백으로 사용됨\n\n이어지는 본문\n\n[여백면]\n\n끝\n", encoding="utf-8")
    out = tmp_path / "blank.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    blank = doc.tables.all[0]
    # A 1x1 table just big enough for the wording, centred on a page of its own — and not in the TOC.
    assert blank.cell(0, 0).text == "이 면은 여백으로 사용됨"
    position = blank.element.find(f"{HP}pos")
    assert (position.get("treatAsChar"), position.get("vertRelTo"), position.get("vertAlign")) == ("0", "PAGE", "CENTER")
    assert (position.get("horzRelTo"), position.get("horzAlign")) == ("PAGE", "CENTER")
    assert blank.element.get("numberingType") == "NONE"
    assert blank.cell(0, 0).margins.to_dict() == {"left": 5660, "right": 5660, "top": 1415, "bottom": 1415}
    width = int(blank.element.find(f"{HP}sz").get("width"))
    assert width >= 5660 * 2 + round(estimate_text_width("이 면은 여백으로 사용됨", 11))
    # The wording is set in 바탕글 (휴먼명조) at the blank page's 11pt, and stays on one line: the table is
    # sized to the count plus a spare character, so the last character cannot tip over to a second line.
    run = blank.cell(0, 0).paragraphs[0].runs[0]
    assert doc.styles.char_property(run.element.get("charPrIDRef")).attributes["height"] == "1100"
    assert doc.styles.font_face(run.element.get("charPrIDRef"), "HANGUL") == "휴먼명조"
    assert measure(blank.cell(0, 0).text, resolve_slot_metrics(blank.cell(0, 0), doc)).lines == 1
    # An empty marker still gives a page, and the block after each blank page starts the next page.
    assert doc.tables.all[1].cell(0, 0).text == ""
    starts = [p.text for p in doc.paragraphs[1:] if p.element.get("pageBreak") == "1"]
    assert starts == ["", "이어지는 본문", "", "끝"]


def test_convert_blank_lines(tmp_path):
    md = tmp_path / "lines.md"
    md.write_text(
        "가\n\n[빈 줄 3]\n\n나\n[빈 줄]\n다\n\n> 인용\n>\n> [빈 줄 2]\n\n[빈 줄 0]\n\n[빈 줄 셋]\n\n[빈 줄 11]\n\n"
        "[빈 줄 10]\n",
        encoding="utf-8",
    )
    out = tmp_path / "lines.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    paragraphs = doc.paragraphs[1:]
    # [빈 줄 N] gives N empty paragraphs, [빈 줄] one, [빈 줄 0] none; any other word, or N over 10, stays text.
    assert [p.text for p in paragraphs] == [
        "가", "", "", "", "나", "", "다", "인용", "", "", "[빈 줄 셋]", "[빈 줄 11]", *[""] * 10
    ]
    body = doc.styles.resolve("본문")
    assert all(int(p.style_id_ref) == body.id for p in paragraphs)
    # In a quote they are placed like its paragraphs: indented, with the bar.
    margin = [int(doc.styles.paragraph_property(p.para_pr_id_ref).margin.left) for p in paragraphs[7:10]]
    assert margin[1:] == [margin[0]] * 2 and margin[0] > BODY_LEFT


def test_convert_blank_page_first(tmp_path):
    md = tmp_path / "blank.md"
    md.write_text("[여백면] 첫 면\n\n본문\n", encoding="utf-8")
    out = tmp_path / "blank.hwpx"

    convert(md, TEMPLATE, out)

    # The blank page is the document's first page: no break before it, only after.
    paragraphs = HwpxDocument.open(out).paragraphs[1:]
    assert [p.text for p in paragraphs if p.element.get("pageBreak") == "1"] == ["본문"]


def test_convert_table_of_contents(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text(
        "# 문서\n\n[TOC]\n\n## 소개\n\n본문\n\n```\n코드\n```\n\n## 사용법\n\n### 세부\n\n#### 4수준\n\n[toc]\n",
        encoding="utf-8",
    )
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    texts = [p.text for p in doc.paragraphs[1:]]
    # The TOC field sits where the first marker was; the second marker adds nothing.
    assert texts[:6] == ["문서", "차례", "1. 문서\t1", "2. 소개\t1", "3. 사용법\t1", "4. 세부\t1"]
    # level 4 is not listed in the TOC; level 1 headings get no empty paragraph before them.
    assert texts[6:] == ["", "", "소개", "본문", "", "", "사용법", "", "세부", "", "4수준"]
    begins = [b for p in doc.paragraphs for b in p.element.iter(f"{HP}fieldBegin") if b.get("type") == "TABLEOFCONTENTS"]
    assert len(begins) == 1
    command = "".join(begins[0].itertext())
    assert "ContentsLevel:int:3" in command
    assert "ContentsMake:uint:31" in command  # every list, in one field
    assert "ContentsHyperlink:bool:1" in command  # entries link to their headings
    assert begins[0].get("dirty") == "1"  # Hancom rebuilds it on open
    # Text in table cells stays on 바탕글: Hancom does not collect it when it rebuilds the TOC (checked in Hancom).
    code = doc.tables.all[0].cell(0, 0).paragraphs[0]
    assert int(code.style_id_ref) == doc.styles.resolve("바탕글").id


def test_convert_toc_without_headings_is_skipped(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text("[TOC]\n\n본문\n", encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:]] == ["본문"]


def test_convert_toc_lists_in_the_order_named(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text("표지\n\n\\newpage\n[toc 그림 제목 표 그림]\n\n# 장\n\n## 절\n", encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # A field per list, in the order named (a repeat is dropped); only the 제목 list keeps heading entries.
    assert [p.text for p in doc.paragraphs[2:]][:8] == [
        "<그림 차례>", "", "<제목 차례>", "1. 장\t1", "2. 절\t1", "", "<표 차례>", "",
    ]
    begins = [b for p in doc.paragraphs for b in p.element.iter(f"{HP}fieldBegin") if b.get("type") == "TABLEOFCONTENTS"]
    commands = [p.text for b in begins for p in b.iter(f"{HP}stringParam") if p.get("name") == "Command"]
    assert [c.split()[0].rsplit(":", 1)[1] for c in commands] == ["4", "17", "2"]  # ContentsMake:uint:N
    for command in commands:
        # As Hancom writes it: "set:N:" with N the length of the rest less one.
        _, _, n, rest = command.split(":", 3)
        assert int(n) == len(rest) - 1
    assert all(b.get("dirty") == "1" for b in begins)
    # The break before the marker lands on the first list's title.
    assert doc.paragraphs[2].element.get("pageBreak") == "1"


def _toc_commands(doc) -> list[str]:
    """The Command of each TOC field in *doc*, without its "TableOfContents:set:N:" head."""
    begins = [b for p in doc.paragraphs for b in p.element.iter(f"{HP}fieldBegin") if b.get("type") == "TABLEOFCONTENTS"]
    commands = [p.text for b in begins for p in b.iter(f"{HP}stringParam") if p.get("name") == "Command"]
    for command in commands:
        _, _, n, rest = command.split(":", 3)
        assert int(n) == len(rest) - 1
    return [command.split(":", 3)[3] for command in commands]


@pytest.mark.parametrize(
    "marker, make, styles",
    [
        # As Hancom writes them: 1 the 제목 list, 16 from 개요 paragraphs, 32 from the styles named.
        ("[TOC 제목]", "17", "ContentsStyles:wstring:0:"),
        ('[TOC 스타일="요약문-본문"]', "33", "ContentsStyles:wstring:7:요약문-본문;"),
        ('[TOC 스타일="요약문-본문; 표준"]', "33", "ContentsStyles:wstring:10:요약문-본문;표준;"),
        ('[TOC 개요 스타일="요약문-본문"]', "49", "ContentsStyles:wstring:7:요약문-본문;"),
    ],
)
def test_convert_toc_collects_styles(tmp_path, marker, make, styles):
    md = tmp_path / "toc.md"
    md.write_text(
        f"{marker}\n\n# 장\n\n::: {{custom-style=\"요약문-본문\"}}\n모을 문단\n:::\n\n본문 문단\n", encoding="utf-8"
    )
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    [command] = _toc_commands(doc)
    assert command.startswith(f"ContentsMake:uint:{make} {styles} ContentsLevel:int:3 ")
    # Its first entries are what it collects; Hancom collects them again when it rebuilds the TOC.
    entries = [p.text.split("\t")[0] for p in doc.paragraphs[2:4]]
    assert entries == {"17": ["1. 장", ""], "33": ["1. 모을 문단", ""], "49": ["1. 장", "2. 모을 문단"]}[make]


def test_convert_toc_styles_with_other_lists(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text('[TOC 그림 스타일="표준;표준" 표 제목]\n\n# 장\n\n::: {custom-style="표준"}\n글\n:::\n', encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    # 스타일="..." names the 제목 list where it stands (제목 after it adds nothing); a name repeated counts once.
    commands = _toc_commands(HwpxDocument.open(out))
    assert [c.split()[0] for c in commands] == ["ContentsMake:uint:4", "ContentsMake:uint:33", "ContentsMake:uint:2"]
    assert "ContentsStyles:wstring:3:표준;" in commands[1]


def test_convert_toc_without_paragraphs_of_its_styles(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text('[TOC 스타일="표준"]\n\n# 장\n', encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    # Nothing to collect (the 개요 heading is not asked for): no TOC, as without headings.
    assert _toc_commands(HwpxDocument.open(out)) == []


def test_unknown_toc_styles():
    markdown = (
        '본문  \n다음 줄\n[TOC 스타일="본문;없는 스타일"]\n\n| [TOC 스타일="표 안"] |\n|---|\n\n'
        '[TOC 스타일="*강조*"]\n\n각주[^1]\n\n[^1]: [TOC 스타일="각주 안"]\n'
    )
    # The marker's line, past a hard break. A [TOC] in a table cell or a footnote, or with emphasis in it,
    # is not a marker: the conversion writes it as text, so it is not warned about.
    assert unknown_styles(markdown, {"본문"}) == [(3, "없는 스타일", "차례에 모을 수 없습니다")]


def test_convert_marker_with_emphasis_is_text(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text('[TOC 스타일="*강조*"]\n\n# 장\n', encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    assert _toc_commands(doc) == []
    assert [p.text for p in doc.paragraphs[1:]] == ['[TOC 스타일="강조"]', "장"]


@pytest.mark.parametrize(
    "marker, makes",
    [("[TOC 링크없음]", ["31"]), ("[TOC 표 링크없음 제목]", ["2", "17"])],
)
def test_convert_toc_without_links(tmp_path, marker, makes):
    md = tmp_path / "toc.md"
    md.write_text(f"{marker}\n\n# 장\n", encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    begins = [b for p in doc.paragraphs for b in p.element.iter(f"{HP}fieldBegin") if b.get("type") == "TABLEOFCONTENTS"]
    commands = [p.text for b in begins for p in b.iter(f"{HP}stringParam") if p.get("name") == "Command"]
    # 링크없음 is not a list: it turns the links off in every field the marker makes.
    assert [c.split()[0].rsplit(":", 1)[1] for c in commands] == makes
    assert all("ContentsHyperlink:bool:0" in c for c in commands)


def test_convert_toc_with_unknown_list_stays_text(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text("# 장\n\n[TOC 목록]\n", encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:]] == ["장", "[TOC 목록]"]


def test_convert_heading_page_breaks_come_from_the_template(tmp_path):
    md = tmp_path / "chapters.md"
    md.write_text("# 첫 장\n\n## 첫째 절\n\n## 둘째 절\n\n# 둘째 장\n\n# 셋째 장\n", encoding="utf-8")
    out = tmp_path / "chapters.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # The converter breaks no page at a heading itself ...
    assert not [p for p in doc.paragraphs if p.element.get("pageBreak") == "1"]
    # ... the bundled template's 개요 1 paragraph shape does ("문단 앞에서 항상 쪽 나눔"); 개요 2 does not.
    # The first heading is on the first page already: its break is off, or that page would be left empty.
    shapes = {p.get("id"): p for p in doc.parts.headers[0].element.iter(f"{HH}paraPr")}
    breaks = {
        p.text: shapes[p.element.get("paraPrIDRef")].find(f"{HH}breakSetting").get("pageBreakBefore")
        for p in doc.paragraphs if p.text
    }
    assert breaks == {"첫 장": "0", "첫째 절": "0", "둘째 절": "0", "둘째 장": "1", "셋째 장": "1"}


def test_convert_page_breaks(tmp_path):
    md = tmp_path / "breaks.md"
    md.write_text(
        "\\newpage\n\n첫 쪽\n\n\\newpage\n\n# 둘째 쪽\n\n\\pagebreak\n[TOC]\n\\newpage\n\n본문\n\n\\newpage\n",
        encoding="utf-8",
    )
    out = tmp_path / "breaks.hwpx"

    convert(md, TEMPLATE, out)

    paragraphs = HwpxDocument.open(out).paragraphs[1:]
    starts = [p.text for p in paragraphs if p.element.get("pageBreak") == "1"]
    # The first one is at the very start, already a new page; the break before [TOC] lands on the
    # TOC's title paragraph, the one after it on "본문"; the last one has nothing after it.
    assert starts == ["둘째 쪽", "차례", "본문"]
    assert [p.text for p in paragraphs][-1] == "본문"


def _new_numbers(paragraph) -> list[str]:
    """The numTypes *paragraph* starts over (hp:newNum), each at 1."""
    news = list(paragraph.element.iter(f"{HP}newNum"))
    assert all(n.get("num") == "1" for n in news)
    return [n.get("numType") for n in news]


def test_convert_caption_numbers_are_hancoms_own(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "numbers.md"
    md.write_text(
        "# 장\n\n[표] 표 캡션\n\n| a |\n|---|\n| 1 |\n\n```\n캡션 없는 코드\n```\n\n"
        "![](a.png)\n\n[그림] 그림 캡션\n\n![](a.png)\n",
        encoding="utf-8",
    )
    out = tmp_path / "numbers.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    tables = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}tbl")]
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    # The caption's number is an hp:autoNum, as Hancom writes it, holding the count as its cached value.
    auto = tables[0].find(f"{HP}caption").find(f".//{HP}autoNum")
    assert (auto.get("numType"), auto.get("num")) == ("TABLE", "1")
    assert auto.find(f"{HP}autoNumFormat").get("type") == "DIGIT"
    assert pics[1].find(f"{HP}caption").find(f".//{HP}autoNum").get("numType") == "PICTURE"
    # Hancom counts every table and picture: those without a caption are kept out of the count.
    assert [t.get("numberingType") for t in tables] == ["TABLE", "NONE"]
    assert [p.get("numberingType") for p in pics] == ["NONE", "PICTURE"]
    # A chapter starts the table and picture numbers over, on its heading.
    assert _new_numbers(doc.paragraphs[1]) == ["PICTURE", "TABLE"]


def test_convert_numbers_captions_through_the_document(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "document.md"
    md.write_text(
        "---\ncaption-numbering: document\n---\n\n"
        "# 첫 장\n\n[표] 하나\n\n| a |\n|---|\n| 1 |\n\n[그림] 하나\n\n![](a.png)\n\n"
        "# 둘째 장\n\n[표] 둘\n\n| b |\n|---|\n| 2 |\n\n[그림] 둘\n\n![](a.png)\n\n"
        "[새 번호 표]\n[표] 다시 하나\n\n| c |\n|---|\n| 3 |\n",
        encoding="utf-8",
    )
    out = tmp_path / "document.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    # No chapter number, and a chapter does not start the count over; [새 번호] still does.
    assert [_text(t.caption.element) for t in doc.tables.all] == ["[표 1] 하나", "[표 2] 둘", "[표 1] 다시 하나"]
    assert [_text(pic.find(f"{HP}caption")) for pic in pics] == ["[그림 1] 하나", "[그림 2] 둘"]
    headings = [p for p in doc.paragraphs if p.text in ("첫 장", "둘째 장")]
    assert [_new_numbers(h) for h in headings] == [[], []]


def test_caption_numbering_from_front_matter(tmp_path):
    def md(name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    plain = md("plain.md", "# 장\n")
    chapter = md("chapter.md", "---\ntitle: 제목\ncaption-numbering: chapter\n---\n")
    document = md("document.md", "---\ncaption-numbering: 'document'\n---\n")
    in_body = md("body.md", "본문\n\ncaption-numbering: document\n")
    unknown = md("unknown.md", "---\ncaption-numbering: section\n---\n")

    assert caption_numbering([plain]) == "chapter"
    assert caption_numbering([chapter]) == "chapter"
    # Quoted as YAML may be; a file that does not say leaves it to the one that does.
    assert caption_numbering([plain, document, document]) == "document"
    # Only the front matter says it, not the body.
    assert caption_numbering([in_body]) == "chapter"
    with pytest.raises(ConversionError, match=r"unknown\.md: caption-numbering .* section"):
        caption_numbering([unknown])
    # Files that disagree: a document has one way, so neither is chosen.
    with pytest.raises(ConversionError, match=r"chapter\.md 는 'chapter', .*document\.md 는 'document'"):
        caption_numbering([chapter, plain, document])


def _style_names(doc) -> dict[str, str]:
    """Each non-empty paragraph's text -> the name of its style."""
    styles = {st.id: st.name for st in doc.styles.values()}
    return {p.text: styles[int(p.style_id_ref)] for p in doc.paragraphs if p.text}


def test_convert_custom_style_div(tmp_path):
    md = tmp_path / "styled.md"
    md.write_text(
        "앞\n\n"
        '::: {custom-style="요약문-본문"}\n가\n\n나\n\n> 인용\n\n## 절\n\n- 항목\n:::\n\n'
        '::::: {custom-style="요약문-본문"}\n바깥\n\n::: {custom-style="표준"}\n안\n:::\n\n다시 바깥\n:::::\n\n'
        '::: {custom-style="없는 스타일"}\n없음\n:::\n\n'
        "::: note\n그냥 글\n:::\n\n뒤\n",
        encoding="utf-8",
    )
    out = tmp_path / "styled.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # Its text paragraphs (in a quote too) take the style; a heading or a list item keeps its own.
    # Inside another div the inner one wins; a style the template lacks leaves them 본문, and any other
    # ":::" block is text as it was.
    assert _style_names(doc) == {
        "앞": "본문", "가": "요약문-본문", "나": "요약문-본문", "인용": "요약문-본문", "절": "개요 2", "항목": "본문",
        "바깥": "요약문-본문", "안": "표준", "다시 바깥": "요약문-본문", "없음": "본문",
        "::: note 그냥 글 :::": "본문", "뒤": "본문",
    }
    # The style's paragraph shape comes along, as for 본문.
    style = doc.styles.resolve("요약문-본문")
    assert next(p for p in doc.paragraphs if p.text == "가").para_pr_id_ref == str(style.para_pr_id_ref)


def test_unknown_styles(tmp_path):
    markdown = (
        "---\nk: v\n---\n"
        '::: {custom-style="본문"}\n가\n:::\n\n'
        '::: {custom-style="없는 스타일"}\n나\n:::\n\n'
        '```\n::: {custom-style="코드 안"}\n```\n'
    )
    # Line numbers count the front matter; a div in a code block is not one.
    assert unknown_styles(markdown, {"본문"}) == [(8, "없는 스타일", "본문 스타일로 넣었습니다")]
    assert "요약문-본문" in paragraph_styles(TEMPLATE) and "본문" in paragraph_styles(TEMPLATE)


def test_convert_restarts_numbers(tmp_path):
    md = tmp_path / "restart.md"
    md.write_text(
        "# 장\n\n[표] 첫째\n\n| a |\n|---|\n| 1 |\n\n"
        "\\newpage\n[새 번호 쪽 표 그림 수식]\n[표] 둘째\n\n| b |\n|---|\n| 2 |\n\n"
        "[새 번호 장]\n\n[새 번호 쪽]\n",
        encoding="utf-8",
    )
    out = tmp_path / "restart.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # The count starts over within the chapter; the chapter number stays.
    assert [_text(t.caption.element) for t in doc.tables.all] == ["[표 1-1] 첫째", "[표 1-1] 둘째"]
    second = next(p for p in doc.paragraphs if p.element.get("pageBreak") == "1")
    assert _new_numbers(second) == ["EQUATION", "PAGE", "PICTURE", "TABLE"]
    # The restarts come before the table the paragraph holds, so it is numbered after them.
    tags = [e.tag for e in second.element.iter(f"{HP}newNum", f"{HP}tbl")]
    assert tags[-1] == f"{HP}tbl"
    # An unknown word leaves the line as text; a restart with nothing after it is dropped.
    assert doc.paragraphs[-1].text == "[새 번호 장]"
    assert _new_numbers(doc.paragraphs[-1]) == []


def test_convert_restarts_numbers_without_a_page_break(tmp_path):
    md = tmp_path / "restart.md"
    md.write_text("앞\n\n[새 번호 쪽]\n\n뒤\n", encoding="utf-8")
    out = tmp_path / "restart.hwpx"

    convert(md, TEMPLATE, out)

    paragraphs = HwpxDocument.open(out).paragraphs[1:]
    assert [p.text for p in paragraphs] == ["앞", "뒤"]
    assert _new_numbers(paragraphs[1]) == ["PAGE"]
    assert paragraphs[1].element.get("pageBreak") != "1"


def test_convert_page_break_takes_no_words(tmp_path):
    md = tmp_path / "words.md"
    md.write_text("앞\n\n\\newpage 쪽숨김 쪽\n", encoding="utf-8")
    out = tmp_path / "words.hwpx"

    convert(md, TEMPLATE, out)

    # The words that went after "\newpage" are "[새 번호]" and "[감추기]" now: the line stays text.
    last = HwpxDocument.open(out).paragraphs[-1]
    assert last.text == "\\newpage 쪽숨김 쪽"
    assert last.element.get("pageBreak") != "1" and _new_numbers(last) == []


def test_convert_restart_before_toc_restarts_numbers_at_the_toc(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text("표지\n\n\\newpage\n[새 번호 쪽]\n[TOC]\n\n# 장\n", encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    paragraphs = HwpxDocument.open(out).paragraphs[1:]
    assert paragraphs[1].text == "차례" and paragraphs[1].element.get("pageBreak") == "1"
    assert _new_numbers(paragraphs[1]) == ["PAGE"]
    heading = next(p for p in paragraphs if p.text == "장")
    assert _new_numbers(heading) == ["PICTURE", "TABLE"]


def test_convert_hides_the_page_number(tmp_path):
    md = tmp_path / "hide.md"
    md.write_text(
        "[감추기]\n표지\n\n\\newpage\n[감추기]\n[새 번호 쪽]\n[TOC]\n\n# 장\n\n\\newpage\n\n본문\n\n"
        "\\newpage\n[감추기]\n",
        encoding="utf-8",
    )
    out = tmp_path / "hide.hwpx"

    convert(md, TEMPLATE, out)
    paragraphs = HwpxDocument.open(out).paragraphs[1:]
    hidden = {p.text: p.element.find(f".//{HP}pageHiding") for p in paragraphs if p.element.find(f".//{HP}pageHiding") is not None}
    # The cover and the TOC (its title paragraph) hide theirs; a plain break and one at the end hide nothing.
    assert list(hidden) == ["표지", "차례"]
    # The cover is the first page as it is: no break before it, but it still hides its number.
    assert paragraphs[0].text == "표지" and paragraphs[0].element.get("pageBreak") != "1"
    for hiding in hidden.values():
        assert (hiding.get("hideHeader"), hiding.get("hideFooter"), hiding.get("hidePageNum")) == ("1", "1", "1")
    toc = paragraphs[1]
    assert _new_numbers(toc) == ["PAGE"]
    # Up front, before the TOC field the paragraph holds.
    first = next(e for e in toc.element.iter() if e.tag in (f"{HP}pageHiding", f"{HP}fieldBegin"))
    assert first.tag == f"{HP}pageHiding"


def test_convert_refuses_invalid_document(tmp_path, monkeypatch):
    from hwpx.tools.validator import ValidationIssue, ValidationReport

    from md2hwpx.convert import ConversionError

    issue = ValidationIssue(part_name="Contents/section0.xml", message="broken", line=3)
    monkeypatch.setattr(HwpxDocument, "validate", lambda self: ValidationReport(("Contents/section0.xml",), (issue,)))
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    out = tmp_path / "doc.hwpx"

    with pytest.raises(ConversionError, match="Contents/section0.xml:3: broken"):
        convert(md, TEMPLATE, out)
    assert not out.exists()


def _paragraph_info(doc, p):
    pp = doc.styles.paragraph_property(p.para_pr_id_ref)
    return p.text, pp.heading.type, int(pp.margin.left)


def test_convert_blocks_inside_list_items(tmp_path):
    md = tmp_path / "nested.md"
    md.write_text(
        "1. 첫 항목\n\n   둘째 문단\n\n   ```py\n   x = 1\n   ```\n\n   | a |\n   |---|\n   | 1 |\n\n"
        "   > 항목 안 인용\n\n2. 둘째 항목\n\n> - 인용 안 목록\n",
        encoding="utf-8",
    )
    out = tmp_path / "nested.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    paragraphs = doc.paragraphs[1:]
    assert [_paragraph_info(doc, p) for p in paragraphs] == [
        ("첫 항목", "NUMBER", BODY_LEFT),
        ("둘째 문단", "NONE", BODY_LEFT + 1000),  # the item's other blocks sit one level in
        ("", "NONE", BODY_LEFT + 1000),  # code block anchor
        ("", "NONE", BODY_LEFT + 1000),  # table anchor
        ("항목 안 인용", "NONE", BODY_LEFT + 1000 + 1000),  # quote inside the item
        ("둘째 항목", "NUMBER", BODY_LEFT),
        ("인용 안 목록", "BULLET", BODY_LEFT + 1000),
    ]
    assert [t.cell(0, 0).text for t in doc.tables.all] == ["x = 1", "a"]
    quote_bar = doc.styles.paragraph_property(paragraphs[4].para_pr_id_ref).border
    assert doc.styles.border_fill_info(quote_bar.border_fill_id_ref).left.type == "SOLID"
    numbering = {doc.styles.paragraph_property(paragraphs[i].para_pr_id_ref).heading.id_ref for i in (0, 5)}
    assert len(numbering) == 1  # "2." numbers on after the item's blocks


def test_convert_numbering_around_nested_lists(tmp_path):
    md = tmp_path / "numbers.md"
    md.write_text("1. 하나\n   - 글머리\n     1. 안쪽 번호\n2. 둘\n", encoding="utf-8")
    out = tmp_path / "numbers.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    heads = [doc.styles.paragraph_property(p.para_pr_id_ref).heading for p in doc.paragraphs[1:]]
    assert [(h.type, h.level) for h in heads] == [("NUMBER", 0), ("BULLET", 1), ("NUMBER", 2), ("NUMBER", 0)]
    # "2." goes on with "1.", not with the inner list under the bullet.
    assert heads[3].id_ref == heads[0].id_ref != heads[2].id_ref


def test_convert_commonmark_blocks(tmp_path):
    md = tmp_path / "commonmark.md"
    md.write_text(
        "제목 하나\n===\n\n제목 둘\n---\n\n문단\n\n---\n\n    들여쓴 코드\n\n"
        "[참조 링크][ref] 와 ![참조 그림][pic]\n\n[ref]: https://example.com\n[pic]: a.png\n",
        encoding="utf-8",
    )
    (tmp_path / "a.png").write_bytes(_png(2, 1))
    out = tmp_path / "commonmark.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    styles = {st.id: st.name for st in doc.styles.values()}
    paragraphs = doc.paragraphs[1:]
    assert [(p.text, styles[int(p.style_id_ref)]) for p in paragraphs] == [
        ("제목 하나", "개요 1"),
        ("", "본문"),  # before the level 2 setext heading
        ("제목 둘", "개요 2"),
        ("문단", "본문"),
        ("", "본문"),  # thematic break after a blank line
        ("", "본문"),  # indented code block anchor
        ("참조 링크 와", "본문"),
        ("", "바탕글"),  # the referenced picture
    ]
    assert doc.tables.all[0].cell(0, 0).text == "들여쓴 코드"
    assert [link["url"] for link in paragraphs[6].hyperlinks] == ["https://example.com"]
    assert len(doc.media.picture_references()) == 1


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


def test_convert_emphasis_and_formatted_footnotes(tmp_path):
    md = tmp_path / "emphasis.md"
    md.write_text(
        "***a** b* 끝[^n]\n\n[^n]: **굵은** 각주\n\n    둘째 문단 [링크](https://example.com)\n",
        encoding="utf-8",
    )
    out = tmp_path / "emphasis.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)

    def marks(run):
        char_pr = doc.styles.char_property(run.element.get("charPrIDRef"))
        return run.text, {m for m in ("bold", "italic") if m in char_pr.child_attributes}

    paragraph = doc.paragraphs[1]
    assert [marks(r) for r in paragraph.runs if r.text][:3] == [("a", {"bold", "italic"}), (" b", {"italic"}), (" 끝", set())]
    note = next(paragraph.element.iter(f"{HP}footNote"))
    body = note.find(f".//{HP}p")
    runs = [(r.find(f"{HP}t"), r.get("charPrIDRef")) for r in body.findall(f"{HP}run")]
    texts = [("".join(t.itertext()), "bold" in doc.styles.char_property(c).child_attributes) for t, c in runs if t is not None]
    assert texts[0] == ("굵은", True)
    assert "둘째 문단" in "".join(note.itertext())
    assert body.find(f".//{HP}lineBreak") is not None  # the second paragraph starts a new line
    assert any(b.get("type") == "HYPERLINK" for b in note.iter(f"{HP}fieldBegin"))


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


def test_convert_lists_after_headings(tmp_path):
    md = tmp_path / "headed.md"
    md.write_text("## 절\n\n- 하나\n  - 둘\n\n### 소절\n\n1. 번호\n", encoding="utf-8")
    out = tmp_path / "headed.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)

    def heading_left(level: int) -> int:
        style = doc.styles.resolve(f"개요 {level}")
        return int(doc.styles.paragraph_property(style.para_pr_id_ref).margin.left)

    # A heading keeps its 개요 style's own indent; its level is not a list level: the lists still start at level 1.
    # The empty 본문 paragraph goes before a sub-heading only, so the leading "## 절" gets none.
    assert [_paragraph_info(doc, p) for p in doc.paragraphs[1:]] == [
        ("", "NONE", BODY_LEFT),  # before the level 2 heading
        ("절", "OUTLINE", heading_left(2)),
        ("하나", "BULLET", BODY_LEFT),
        ("둘", "BULLET", BODY_LEFT + 1000),
        ("", "NONE", BODY_LEFT),  # before the level 3 heading
        ("소절", "OUTLINE", heading_left(3)),
        ("번호", "NUMBER", BODY_LEFT),
    ]
