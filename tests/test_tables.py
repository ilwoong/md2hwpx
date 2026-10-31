"""Tables and code blocks (a code block is a 1x1 table): layout, widths, row heights, highlighting."""

from hwpx import HwpxDocument
from hwpx.form_fit.measure import estimate_text_width, measure, resolve_slot_metrics
from hwpx.layout import lint_layout

from helpers import HP, TEXT_WIDTH
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert


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
