import struct
import zlib
from pathlib import Path

from hwpx import HwpxDocument

from md2hwpx.convert import convert

TEMPLATE = Path(__file__).resolve().parent.parent / "template.hwpx"


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"")


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


def test_convert_diagram_images_fill_body_width(tmp_path):
    for name in ("mermaid-0123456789ab.png", "plantuml-0123456789ab.png", "photo.png"):
        (tmp_path / name).write_bytes(_png())
    md = tmp_path / "diagram.md"
    md.write_text("![](mermaid-0123456789ab.png)\n\n![](plantuml-0123456789ab.png)\n\n![](photo.png)\n", encoding="utf-8")
    out = tmp_path / "diagram.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # template: A4 width 59528, page margins 8504 each, 본문 left margin 1500
    widths = [ref.width for ref in doc.media.picture_references()]
    assert widths == [59528 - 8504 * 2 - 1500, 59528 - 8504 * 2 - 1500, 14400]


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
