import struct
import zlib
from pathlib import Path

from hwpx import HwpxDocument

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
    assert [align(cell) for cell in body[:3]] == ["LEFT", "CENTER", "RIGHT"]
    assert doc.paragraphs[-1].text == "뒤 문단"


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
    body_height = 84188 - 5668 - 4252 * 3
    sizes = [(ref.width, ref.height) for ref in HwpxDocument.open(out).media.picture_references()]
    assert sizes == [
        (body_width, body_width // 2),
        (round(body_height / 10), body_height),
        (200 * 75, 100 * 75),
        (body_width, round(body_width / 4)),
        (round((body_height - 2500) / 20), body_height - 2500),
        (400 * 75, 300 * 75),
        (300 * 75, 150 * 75),
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
