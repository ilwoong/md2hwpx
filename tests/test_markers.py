"""Marker lines about pages and paragraphs: page breaks, number restarts, page hiding, blank pages, blank lines."""

import re
import xml.etree.ElementTree as ET

from hwpx import HwpxDocument
from hwpx.form_fit.measure import estimate_text_width, measure, resolve_slot_metrics

from helpers import BODY_LEFT, HP, _new_numbers, _text
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert


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
    assert measure(blank.cell(0, 0).text, resolve_slot_metrics(blank.cell(0, 0), doc, safety=1.0)).lines == 1
    # An empty marker still gives a page, and the block after each blank page starts the next page.
    assert doc.tables.all[1].cell(0, 0).text == ""
    starts = [p.text for p in doc.paragraphs[1:] if p.element.get("pageBreak") == "1"]
    assert starts == ["", "이어지는 본문", "", "끝"]
    # A blank page hides its page number, but keeps its header and footer.
    for table in doc.tables.all:
        hiding = table.paragraph.element.find(f".//{HP}pageHiding")
        assert (hiding.get("hideHeader"), hiding.get("hideFooter"), hiding.get("hidePageNum")) == ("0", "0", "1")
    hidden = [p.text for p in doc.paragraphs[1:] if p.element.find(f".//{HP}pageHiding") is not None]
    assert hidden == ["", ""]


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


def test_convert_markers_in_code_are_text(tmp_path):
    markers = ["[TOC]", "\\newpage", "[새 번호 쪽 표]", "[감추기]", "[여백면] 문구", "[빈 줄 3]"]
    lines = "\n".join(markers)
    indented = "\n".join(f"    {m}" for m in markers)
    inline = "\n\n".join(f"`{m}`" for m in markers)
    md = tmp_path / "code.md"
    md.write_text(f"본문\n\n```\n{lines}\n```\n\n{indented}\n\n{inline}\n", encoding="utf-8")
    out = tmp_path / "code.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # In a fenced or indented code block, or in inline code, a marker is only text: the code blocks hold
    # it as written, and inline code is a paragraph of its own.
    assert [t.cell(0, 0).text for t in doc.tables.all] == [lines, lines]
    texts = [p.text for p in doc.paragraphs[1:]]
    assert texts[-len(markers):] == markers
    # Nothing a marker does: no TOC field, page break, number restart or page hiding, no extra paragraphs.
    assert len(texts) == 1 + 2 + len(markers)  # 본문, a paragraph per code block, the inline code
    element = doc.sections[0].element
    assert not any(b.get("type") == "TABLEOFCONTENTS" for b in element.iter(f"{HP}fieldBegin"))
    assert [p.text for p in doc.paragraphs[1:] if p.element.get("pageBreak") == "1"] == []
    assert next(element.iter(f"{HP}newNum"), None) is None
    assert next(element.iter(f"{HP}pageHiding"), None) is None


def test_escaped_notations_stay_text(tmp_path):
    md = tmp_path / "escaped.md"
    md.write_text(
        "# 장\n\n\\[감추기]\n\n\\[빈 줄 2]\n\n\\[TOC]\n\n\\\\newpage\n\n\\[새 번호 쪽]\n\n\\[여백면] 문구\n\n"
        "\\[표] 표 아닌 제목\n| a | \\[@이름] |\n|---|---|\n\n"
        "[표 #이름] 제목\n| b |\n|---|\n\n"
        "\\[@이름] 과 [@이름], 각주[^n]\n\n[^n]: 각주 \\[@이름]\n\n"
        "[여백면] 문구 \\[x]\n\n![그림 \\[x\\]](gone.png)\n",
        encoding="utf-8",
    )
    out = tmp_path / "escaped.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    section = doc.sections[-1].element
    texts = [p.text for p in doc.paragraphs[1:]]
    # An escaped "[" or "\" keeps the notation it starts as its text, without the backslash, and does nothing.
    for line in ("[감추기]", "[빈 줄 2]", "[TOC]", "\\newpage", "[새 번호 쪽]", "[여백면] 문구", "[표] 표 아닌 제목"):
        assert line in texts
    assert "TableOfContents" not in ET.tostring(section, encoding="unicode")
    assert section.find(f".//{HP}newNum[@numType='PAGE']") is None
    # The one page hidden and the only page breaks are the real blank page's, which keeps an escaped "[".
    assert len(list(section.iter(f"{HP}pageHiding"))) == 1
    assert [p.text for p in doc.paragraphs[1:] if p.element.get("pageBreak") == "1"] == ["", ""]
    tables = doc.tables.all
    assert [t.cell(0, 1).text if t.column_count > 1 else t.cell(0, 0).text for t in tables] == ["[@이름]", "b", "문구 [x]"]
    # Only the named table has a caption above it; an escaped reference stays text, the other reads its number.
    assert tables[0].element.get("numberingType") == "NONE"
    assert "[@이름] 과 표 1-1, 각주" in texts
    assert "각주 [@이름]" in _text(section)
    # An image's alt text keeps its escaped "[" in its caption.
    assert "그림 [x]" in _text(section)
    # The escaped characters never reach the HWPX as anything but themselves.
    assert not re.search("[\ue000-\uf8ff]", _text(section))
