import struct
import zlib
from pathlib import Path

import pytest
from hwpx import HwpxDocument
from hwpx.layout import lint_layout

from md2hwpx.convert import convert

TEMPLATE = Path(__file__).resolve().parent.parent / "template.hwpx"


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
        ("소제목", "개요 2"),
        ("", "본문"),  # picture
        ("", "본문"),  # code block table anchor
    ]
    # Each paragraph uses its style's own paragraph shape, not the previous heading's.
    assert all(int(p.para_pr_id_ref) == styles[int(p.style_id_ref)].para_pr_id_ref for p in paragraphs)
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
    margin = table.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}outMargin").attrib
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
    # A line of space above and below, none at the sides.
    margin = table.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}outMargin").attrib
    assert margin == {"left": "0", "right": "0", "top": "1400", "bottom": "1400"}
    # The header row repeats on each page the table runs on to.
    assert table.element.get("repeatHeader") == "1"
    assert [cell.element.get("header") for cell in header + body] == ["1"] * 4 + ["0"] * 4
    assert [align(cell) for cell in body[:3]] == ["LEFT", "CENTER", "RIGHT"]
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
        assert table.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}pos").get("horzRelTo") == "PARA"
    assert not [f for f in lint_layout(out.read_bytes()).findings if f.code == "TABLE_TALLER_THAN_PAGE"]


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
    assert (caption.side, caption.text) == ("TOP", "[표 1] 표 캡션")
    assert doc.tables.all[1].caption.text == "[표 2] 둘째 표"
    code_caption = doc.tables.all[2].caption
    assert (code_caption.side, code_caption.text) == ("TOP", "[표 3] 코드 캡션")
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    pic = next(e for p in doc.paragraphs for e in p.element.iter(f"{hp}pic"))
    pic_caption = pic.find(f"{hp}caption")
    assert pic_caption.get("side") == "BOTTOM"
    assert "".join(pic_caption.itertext()) == "[그림 1] 그림 캡션"
    assert [p.text for p in doc.paragraphs if p.text] == ["[표] 표가 뒤따르지 않음", "문단"]

    style = doc.styles.resolve("표그림제목")
    caption_paragraphs = [p for t in doc.tables.all for p in t.caption.paragraphs]
    for p in caption_paragraphs:
        assert (int(p.style_id_ref), int(p.para_pr_id_ref), int(p.char_pr_id_ref)) == (
            style.id, style.para_pr_id_ref, style.char_pr_id_ref
        )
    pic_p = pic_caption.find(f".//{hp}p")
    assert (pic_p.get("styleIDRef"), pic_p.get("paraPrIDRef")) == (str(style.id), str(style.para_pr_id_ref))
    assert pic_p.find(f"{hp}run").get("charPrIDRef") == str(style.char_pr_id_ref)


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

    # template: A4 59528 x 84188, margins left/right 8504, top 5668, bottom/header/footer 4252,
    # 본문 left margin 1500
    body_width = 59528 - 8504 * 2 - 1500
    # less a line of space above and below the picture
    body_height = 84188 - 5668 - 4252 * 3 - 1400 * 2
    doc = HwpxDocument.open(out)
    sizes = [(ref.width, ref.height) for ref in doc.media.picture_references()]
    assert sizes == [
        (body_width, body_width // 2),
        (round(body_height / 10), body_height),
        (200 * 75, 100 * 75),
        (body_width, round(body_width / 4)),
        (round((body_height - 2500) / 20), body_height - 2500),
        (400 * 75, 300 * 75),
        (300 * 75, 150 * 75),
    ]
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    margins = [pic.find(f"{hp}outMargin").attrib for p in doc.paragraphs for pic in p.element.iter(f"{hp}pic")]
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
    body_width = 59528 - 8504 * 2 - 1500

    def mm(value):
        return round(value * 7200 / 25.4)

    sizes = [(ref.width, ref.height) for ref in doc.media.picture_references()]
    assert sizes == [
        (body_width // 2, body_width // 4),
        (mm(80), round(mm(80) / 2)),
        (mm(40) * 2, mm(40)),
        (mm(30), mm(30)),
        (body_width, body_width // 2),
        (body_width, body_width // 2),
        (7500, 3750),
    ]
    # The attribute block is not left as text.
    assert [p.text for p in doc.paragraphs if "뒤" in p.text] == ["뒤"] * len(attributes)


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
    assert runs(table.caption.paragraphs[0]) == [("[표 1] ", False), ("md2hwpx", True), (" 표", False)]
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
    # Weights: "번호" 4, the description 49 (21 Hangul x 2 + 7 spaces), "abcdefgh" 8 (backticks not counted).
    assert sum(widths) == 59528 - 8504 * 2
    assert widths[0] < widths[2] < widths[1]
    assert abs(widths[1] / widths[0] - 49 / 4) < 0.01
    assert [table.cell(1, c).width for c in range(3)] == widths


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
        ("첫째 code 이어지는 줄", "BULLET", 1, 1500),
        ("둘째", "BULLET", 1, 1500),
        ("하위", "BULLET", 2, 2500),
        ("하위의 하위", "BULLET", 3, 3500),
        ("4단계", "BULLET", 3, 3500),  # deeper than 3 stays at level 3
        ("셋부터", "NUMBER", 1, 1500),
        ("넷", "NUMBER", 1, 1500),
        ("하위 번호", "NUMBER", 2, 2500),
        ("다섯", "NUMBER", 1, 1500),
        ("문단 2. 문단에 이어짐", "NONE", 1, 1500),  # only "1." can interrupt a paragraph
        ("새 목록", "NUMBER", 1, 1500),
    ]
    assert all(int(p.style_id_ref) == body.id for p in paragraphs)
    numbering = [doc.styles.paragraph_property(p.para_pr_id_ref).heading.id_ref for p in paragraphs]
    # One numbering for the whole "3." list, sub-level included; the later list starts a new one.
    assert len(set(numbering[5:9])) == 1
    assert numbering[10] != numbering[5]
    hh = "{http://www.hancom.co.kr/hwpml/2011/head}"
    header = doc.oxml.headers[0].element
    first_list = header.find(f".//{hh}numbering[@id='{numbering[5]}']")
    assert first_list.find(f"{hh}paraHead[@level='1']").get("start") == "3"


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
    assert [p.text for p in doc.paragraphs[1:]] == ["본문", "", "title: x"]
    assert int(doc.paragraphs[3].style_id_ref) == doc.styles.resolve("개요 2").id
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
        ("첫 줄 둘째 줄", 1500 + 1000, "SOLID", True),
        ("새 문단", 1500 + 1000, "SOLID", True),
        ("중첩", 1500 + 2000, "SOLID", True),
    ]
    assert all(int(p.style_id_ref) == body.id for p in paragraphs)
    bold = [r.text for r in paragraphs[0].runs if "bold" in doc.styles.char_property(r.element.get("charPrIDRef")).child_attributes]
    assert bold == ["둘째"]
    assert info(paragraphs[3])[:2] == ("문단", 1500)


def test_convert_hard_line_breaks(tmp_path):
    md = tmp_path / "breaks.md"
    md.write_text(
        "공백 두 칸  \n백슬래시\\\n**굵게  \n이어짐** 끝\n\n- 항목  \n  둘째 줄\n\n> 인용\\\n> 둘째\n",
        encoding="utf-8",
    )
    out = tmp_path / "breaks.hwpx"

    convert(md, TEMPLATE, out)

    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    paragraphs = HwpxDocument.open(out).paragraphs[1:]

    def lines(p):
        # hp:lineBreak inside hp:t marks a line break; text() reads it as "\n".
        return sum(1 for _ in p.element.iter(f"{hp}lineBreak")) + 1, p.text.replace("\n", "|")

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
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

    def sequence(p):
        """Runs' text and footnotes (as their text in braces) in document order."""
        items = []
        for run in p.element.findall(f"{hp}run"):
            for child in run:
                if child.tag == f"{hp}t" and child.text:
                    items.append(child.text)
                elif child.tag == f"{hp}ctrl" and child.find(f"{hp}footNote") is not None:
                    note = child.find(f"{hp}footNote")
                    items.append("{" + "".join(note.find(f".//{hp}subList").itertext()) + "}")
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


def test_convert_table_of_contents(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text(
        "# 문서\n\n[TOC]\n\n## 소개\n\n본문\n\n```\n코드\n```\n\n## 사용법\n\n### 세부\n\n#### 4수준\n\n[toc]\n",
        encoding="utf-8",
    )
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    texts = [p.text for p in doc.paragraphs[1:]]
    # The TOC field sits where the first marker was; the second marker adds nothing.
    assert texts[:6] == ["문서", "차례", "1. 문서\t1", "2. 소개\t1", "3. 사용법\t1", "4. 세부\t1"]
    assert texts[6:] == ["", "소개", "본문", "", "사용법", "세부", "4수준"]  # level 4 is not listed
    begins = [b for p in doc.paragraphs for b in p.element.iter(f"{hp}fieldBegin") if b.get("type") == "TABLEOFCONTENTS"]
    assert len(begins) == 1
    command = "".join(begins[0].itertext())
    assert "ContentsLevel:int:3" in command
    assert begins[0].get("dirty") == "1"  # Hancom rebuilds it on open
    # Body text in table cells is moved off 바탕글 so Hancom does not list it in the TOC.
    code = doc.tables.all[0].cell(0, 0).paragraphs[0]
    assert int(code.style_id_ref) == doc.styles.resolve("본문").id


def test_convert_toc_without_headings_is_skipped(tmp_path):
    md = tmp_path / "toc.md"
    md.write_text("[TOC]\n\n본문\n", encoding="utf-8")
    out = tmp_path / "toc.hwpx"

    convert(md, TEMPLATE, out)

    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:]] == ["본문"]


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
    # The break before [TOC] lands on the TOC's title paragraph, the one after it on "본문";
    # the last one has nothing after it.
    assert starts == ["첫 쪽", "둘째 쪽", "차례", "본문"]
    assert [p.text for p in paragraphs][-1] == "본문"


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
        ("첫 항목", "NUMBER", 1500),
        ("둘째 문단", "NONE", 1500 + 1000),  # the item's other blocks sit one level in
        ("", "NONE", 1500 + 1000),  # code block anchor
        ("", "NONE", 1500 + 1000),  # table anchor
        ("항목 안 인용", "NONE", 1500 + 1000 + 1000),  # quote inside the item
        ("둘째 항목", "NUMBER", 1500),
        ("인용 안 목록", "BULLET", 1500 + 1000),
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
        ("제목 둘", "개요 2"),
        ("문단", "본문"),
        ("", "본문"),  # thematic break after a blank line
        ("", "본문"),  # indented code block anchor
        ("참조 링크 와", "본문"),
        ("", "본문"),  # the referenced picture
    ]
    assert doc.tables.all[0].cell(0, 0).text == "들여쓴 코드"
    assert [link["url"] for link in paragraphs[5].hyperlinks] == ["https://example.com"]
    assert len(doc.media.picture_references()) == 1


def test_convert_image_inside_text(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(2, 1))
    md = tmp_path / "inline.md"
    md.write_text("앞 문장 ![](a.png) 뒤 문장\n\n[그림] 캡션\n![](a.png)\n", encoding="utf-8")
    out = tmp_path / "inline.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    assert [p.text for p in doc.paragraphs[1:]] == ["앞 문장", "", "뒤 문장", ""]
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{hp}pic")]
    assert len(pics) == 2
    assert pics[0].find(f"{hp}caption") is None
    assert "".join(pics[1].find(f"{hp}caption").itertext()) == "[그림 1] 캡션"


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
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    note = next(paragraph.element.iter(f"{hp}footNote"))
    body = note.find(f".//{hp}p")
    runs = [(r.find(f"{hp}t"), r.get("charPrIDRef")) for r in body.findall(f"{hp}run")]
    texts = [("".join(t.itertext()), "bold" in doc.styles.char_property(c).child_attributes) for t, c in runs if t is not None]
    assert texts[0] == ("굵은", True)
    assert "둘째 문단" in "".join(note.itertext())
    assert body.find(f".//{hp}lineBreak") is not None  # the second paragraph starts a new line
    assert any(b.get("type") == "HYPERLINK" for b in note.iter(f"{hp}fieldBegin"))


def test_convert_pictures_in_list_items(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(2, 1))
    md = tmp_path / "list-pictures.md"
    md.write_text("1. 단계\n\n   ![](a.png)\n2. ![](a.png)\n", encoding="utf-8")
    out = tmp_path / "list-pictures.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    assert [_paragraph_info(doc, p) for p in doc.paragraphs[1:]] == [
        ("단계", "NUMBER", 1500),
        ("", "NONE", 1500 + 1000),  # picture under the item
        ("", "NUMBER", 1500),  # an item holding only a picture keeps its number ...
        ("", "NONE", 1500 + 1000),  # ... and the picture follows
    ]
    assert len(doc.media.picture_references()) == 2


def test_convert_lists_after_headings(tmp_path):
    md = tmp_path / "headed.md"
    md.write_text("## 절\n\n- 하나\n  - 둘\n\n### 소절\n\n1. 번호\n", encoding="utf-8")
    out = tmp_path / "headed.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # A heading's level is not a list level: the lists still start at level 1.
    assert [_paragraph_info(doc, p) for p in doc.paragraphs[1:]] == [
        ("절", "OUTLINE", 1000 * 2),
        ("하나", "BULLET", 1500),
        ("둘", "BULLET", 1500 + 1000),
        ("소절", "OUTLINE", 1000 * 3),
        ("번호", "NUMBER", 1500),
    ]
