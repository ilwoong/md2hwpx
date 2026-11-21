"""Math: "$...$" and "$$...$$" as Hancom equations, and what cannot be one left as text."""

import pytest
from hwpx import HwpxDocument

from helpers import HP
from md2hwpx.cli import main
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert
from md2hwpx.source import footnote_leftovers, unsupported_math


def _convert(tmp_path, markdown: str) -> HwpxDocument:
    md = tmp_path / "doc.md"
    md.write_text(markdown, encoding="utf-8")
    convert(md, TEMPLATE, tmp_path / "doc.hwpx")
    return HwpxDocument.open(tmp_path / "doc.hwpx")


def _scripts(element) -> list[str]:
    return [script.text for script in element.iter(f"{HP}script")]


def test_inline_math_is_an_equation_among_the_text(tmp_path):
    doc = _convert(tmp_path, r"값은 $\frac{a+b}{c}$ 이고 **굵은 $x_i^2$** 이다." + "\n")

    paragraph = doc.paragraphs[1]
    # In order: text, the equation, text. The LaTeX is not left in the text.
    assert paragraph.text == "값은  이고 굵은  이다."
    assert _scripts(paragraph.element) == ["{a + b} over {c}", "x _{i} ^{2}"]
    runs = paragraph.element.findall(f"{HP}run")
    assert [("t" if run.find(f"{HP}t") is not None else "equation") for run in runs] == [
        "t", "equation", "t", "t", "equation", "t",
    ]
    for equation in paragraph.element.iter(f"{HP}equation"):
        # Laid out as a character, at the text's size (본문: 10pt), and out of Hancom's count of equations.
        assert equation.find(f"{HP}pos").get("treatAsChar") == "1"
        assert equation.get("baseUnit") == "1000"
        assert equation.get("numberingType") == "NONE"
    # The equation in bold text is in the bold run's character shape.
    assert runs[4].get("charPrIDRef") == runs[3].get("charPrIDRef") != runs[0].get("charPrIDRef")


def test_math_block_is_a_centred_paragraph(tmp_path):
    doc = _convert(tmp_path, "앞\n\n$$\n\\sum_{i=1}^{n} x_i\n$$\n\n$$a = b$$\n\n뒤\n")

    styles = {st.id: st for st in doc.styles.values()}
    paragraphs = doc.paragraphs[1:]
    assert [_scripts(p.element) for p in paragraphs] == [[], ["sum _{i = 1} ^{n} x _{i}"], ["a = b"], []]
    for paragraph in paragraphs[1:3]:
        assert paragraph.text == ""
        assert styles[int(paragraph.style_id_ref)].name == "바탕글"
        assert doc.styles.paragraph_property(paragraph.para_pr_id_ref).align.horizontal == "CENTER"


def test_math_where_inline_text_goes(tmp_path):
    doc = _convert(
        tmp_path,
        "# 제목 $E = mc^2$\n\n| 식 |\n|---|\n| $\\sqrt{x}$ |\n\n> 인용 $a \\le b$\n\n- 항목 $\\alpha$\n\n  $$\\beta$$\n\n"
        "각주[^1] 와 문장 안의 $$x$$.\n\n[^1]: 각주 $y^2$.\n",
    )

    assert _scripts(doc.sections[0].element) == ["E = mc ^{2}", "sqrt {x}", "a leq b", "alpha", "beta", "y ^{2}", "x"]
    # In a heading, at the heading's size.
    heading = doc.paragraphs[1].element.find(f".//{HP}equation")
    size = doc.styles.char_property(doc.paragraphs[1].char_pr_id_ref).attributes["height"]
    assert heading.get("baseUnit") == size != "1000"


@pytest.mark.parametrize("text", [
    "가격은 $5 와 $10 이다.",  # a "$" next to a space on its inside
    "3$x$4",  # a "$" next to a digit on its outside
    r"\$x\$",  # escaped
    "$ x $",
])
def test_dollars_that_are_not_math(tmp_path, text):
    doc = _convert(tmp_path, text + "\n")

    assert doc.paragraphs[1].text == text.replace("\\", "")
    assert _scripts(doc.sections[0].element) == []


def test_math_in_code_stays_code(tmp_path):
    doc = _convert(tmp_path, "`$x^2$` 와\n\n```\n$$\ny\n$$\n```\n")

    assert doc.paragraphs[1].text == "$x^2$ 와"
    assert _scripts(doc.sections[0].element) == []


def test_unsupported_math_stays_text(tmp_path):
    markdown = (
        "---\nk: v\n---\n"
        "되는 $x^2$ 와\n안 되는 $\\mathbb{R}^n$.\n\n"
        "$$\n\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}\n$$\n\n"
        "| 식 |\n|---|\n| $\\mathcal{L}$ |\n"
    )
    doc = _convert(tmp_path, markdown)

    # As written, where the equation would be.
    assert doc.paragraphs[1].text == "되는  와 안 되는 $\\mathbb{R}^n$."
    assert doc.paragraphs[2].text == "$$\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}$$"
    assert _scripts(doc.sections[0].element) == ["x ^{2}"]
    assert "$\\mathcal{L}$" in "".join(t.text or "" for t in doc.sections[0].element.iter(f"{HP}t"))
    assert unsupported_math(markdown) == [
        (5, "$\\mathbb{R}^n$"),
        (8, "$$\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}$$"),
        (13, "$\\mathcal{L}$"),
    ]


def test_math_block_in_a_footnote_is_left_out():
    assert footnote_leftovers("글[^1]\n\n[^1]: 각주\n\n    $$a$$\n") == [(5, "수식 블록은")]


def test_cli_warns_about_unsupported_math(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("$x$ 와 $\\mathbb{R}$\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: 경고: {md}:1: 수식($\\mathbb{{R}}$)은 한/글 수식으로 바꿀 수 없어 글자로 넣었습니다",
    ]
