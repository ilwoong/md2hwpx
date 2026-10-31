"""Marker lines about pages and paragraphs: page breaks, number restarts, page hiding, blank pages, blank lines."""

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
