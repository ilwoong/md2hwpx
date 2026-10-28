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
    md.write_text("```python\ndef f():\n    return 1\n```\n", encoding="utf-8")
    out = tmp_path / "code.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    table = doc.tables.all[0]
    assert (table.row_count, table.column_count) == (1, 1)
    paragraphs = table.cell(0, 0).paragraphs
    assert [p.text for p in paragraphs] == ["def f():", "    return 1"]
    for p in paragraphs:
        run = doc.styles.char_property(p.char_pr_id_ref)
        assert run.attributes["height"] == "900"
        assert doc.styles.font_face(p.char_pr_id_ref, "HANGUL") == "D2Coding"
        assert doc.styles.paragraph_property(p.para_pr_id_ref).line_spacing.value == 140


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
