"""The table of contents a [TOC] marker asks for."""

import pytest
from hwpx import HwpxDocument

from helpers import HP
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert
from md2hwpx.source import unknown_styles


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
