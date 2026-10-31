"""Converting Markdown to HWPX: text blocks, lists, quotes, footnotes, styles and validation."""

import pytest
from hwpx import HwpxDocument

from helpers import BODY_LEFT, HH, HP, _paragraph_info, _png
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert, paragraph_styles
from md2hwpx.source import unknown_styles


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
