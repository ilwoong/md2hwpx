"""Table and picture captions, and how they are numbered."""

from pathlib import Path

import pytest
from hwpx import HwpxDocument

from helpers import HP, _new_numbers, _png, _text
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert
from md2hwpx.source import ConversionError, caption_numbering


def test_convert_caption_numbers_restart_in_each_chapter(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "chapters.md"
    md.write_text(
        "# 1장\n\n[표] 첫 표\n| a |\n|---|\n| 1 |\n\n[그림] 첫 그림\n![](a.png)\n\n"
        "## 1.1\n\n[표] 둘째 표\n| b |\n|---|\n| 2 |\n\n"
        "# 2장\n\n[표] 장2 표1\n| c |\n|---|\n| 3 |\n\n[그림] 장2 그림1\n![](a.png)\n",
        encoding="utf-8",
    )
    out = tmp_path / "chapters.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    # The number is the chapter (level 1 heading) and the object's own count within it.
    assert [_text(t.caption.element) for t in doc.tables.all] == ["[표 1-1] 첫 표", "[표 1-2] 둘째 표", "[표 2-1] 장2 표1"]
    pictures = [_text(pic.find(f"{HP}caption")) for p in doc.paragraphs for pic in p.element.iter(f"{HP}pic")]
    assert pictures == ["[그림 1-1] 첫 그림", "[그림 2-1] 장2 그림1"]


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
    # No level 1 heading here, so nothing is in a chapter and the numbers stay plain.
    assert (caption.side, _text(caption.element)) == ("TOP", "[표 1] 표 캡션")
    assert _text(doc.tables.all[1].caption.element) == "[표 2] 둘째 표"
    code_caption = doc.tables.all[2].caption
    assert (code_caption.side, _text(code_caption.element)) == ("TOP", "[표 3] 코드 캡션")
    pic = next(e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic"))
    pic_caption = pic.find(f"{HP}caption")
    assert pic_caption.get("side") == "BOTTOM"
    assert _text(pic_caption) == "[그림 1] 그림 캡션"
    assert [p.text for p in doc.paragraphs if p.text] == ["[표] 표가 뒤따르지 않음", "문단"]

    style = doc.styles.resolve("표그림 제목")
    caption_paragraphs = [p for t in doc.tables.all for p in t.caption.paragraphs]
    for p in caption_paragraphs:
        assert (int(p.style_id_ref), int(p.para_pr_id_ref), int(p.char_pr_id_ref)) == (
            style.id, style.para_pr_id_ref, style.char_pr_id_ref
        )
    pic_p = pic_caption.find(f".//{HP}p")
    assert (pic_p.get("styleIDRef"), pic_p.get("paraPrIDRef")) == (str(style.id), str(style.para_pr_id_ref))
    assert pic_p.find(f"{HP}run").get("charPrIDRef") == str(style.char_pr_id_ref)


def test_convert_alt_text_captions(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "alt.md"
    md.write_text(
        "![대체 텍스트 제목](a.png)\n\n"
        "[그림] 줄로 쓴 제목\n![대체 텍스트](a.png)\n\n"  # the "[그림]" line comes first
        "![](a.png)\n\n"  # no alt text: no caption, not counted
        "![ *기울임* 제목 ](a.png){width=50%}\n\n"
        "# 헤딩 ![헤딩 안 그림](a.png)\n",  # alt text in a heading stays its text
        encoding="utf-8",
    )
    out = tmp_path / "alt.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    captions = [pic.find(f"{HP}caption") for pic in pics]
    assert [c is not None and _text(c) for c in captions] == [
        "[그림 1] 대체 텍스트 제목",
        "[그림 2] 줄로 쓴 제목",
        False,
        "[그림 3] 기울임 제목",
    ]
    assert captions[0].get("side") == "BOTTOM"
    italic = [
        run.get("charPrIDRef") for run in captions[3].iter(f"{HP}run") if "".join(run.itertext()) == "기울임"
    ]
    assert italic and "italic" in doc.styles.char_property(italic[0]).child_attributes
    assert "헤딩 헤딩 안 그림" in [p.text for p in doc.paragraphs]


def test_convert_caption_numbers_are_hancoms_own(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "numbers.md"
    md.write_text(
        "# 장\n\n[표] 표 캡션\n\n| a |\n|---|\n| 1 |\n\n```\n캡션 없는 코드\n```\n\n"
        "![](a.png)\n\n[그림] 그림 캡션\n\n![](a.png)\n",
        encoding="utf-8",
    )
    out = tmp_path / "numbers.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    tables = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}tbl")]
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    # The caption's number is an hp:autoNum, as Hancom writes it, holding the count as its cached value.
    auto = tables[0].find(f"{HP}caption").find(f".//{HP}autoNum")
    assert (auto.get("numType"), auto.get("num")) == ("TABLE", "1")
    assert auto.find(f"{HP}autoNumFormat").get("type") == "DIGIT"
    assert pics[1].find(f"{HP}caption").find(f".//{HP}autoNum").get("numType") == "PICTURE"
    # Hancom counts every table and picture: those without a caption are kept out of the count.
    assert [t.get("numberingType") for t in tables] == ["TABLE", "NONE"]
    assert [p.get("numberingType") for p in pics] == ["NONE", "PICTURE"]
    # A chapter starts the table and picture numbers over, on its heading.
    assert _new_numbers(doc.paragraphs[1]) == ["PICTURE", "TABLE"]


def test_convert_numbers_captions_through_the_document(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    md = tmp_path / "document.md"
    md.write_text(
        "---\ncaption-numbering: document\n---\n\n"
        "# 첫 장\n\n[표] 하나\n\n| a |\n|---|\n| 1 |\n\n[그림] 하나\n\n![](a.png)\n\n"
        "# 둘째 장\n\n[표] 둘\n\n| b |\n|---|\n| 2 |\n\n[그림] 둘\n\n![](a.png)\n\n"
        "[새 번호 표]\n[표] 다시 하나\n\n| c |\n|---|\n| 3 |\n",
        encoding="utf-8",
    )
    out = tmp_path / "document.hwpx"

    convert(md, TEMPLATE, out)

    doc = HwpxDocument.open(out)
    pics = [e for p in doc.paragraphs for e in p.element.iter(f"{HP}pic")]
    # No chapter number, and a chapter does not start the count over; [새 번호] still does.
    assert [_text(t.caption.element) for t in doc.tables.all] == ["[표 1] 하나", "[표 2] 둘", "[표 1] 다시 하나"]
    assert [_text(pic.find(f"{HP}caption")) for pic in pics] == ["[그림 1] 하나", "[그림 2] 둘"]
    headings = [p for p in doc.paragraphs if p.text in ("첫 장", "둘째 장")]
    assert [_new_numbers(h) for h in headings] == [[], []]


def test_caption_numbering_from_front_matter(tmp_path):
    def md(name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    plain = md("plain.md", "# 장\n")
    chapter = md("chapter.md", "---\ntitle: 제목\ncaption-numbering: chapter\n---\n")
    document = md("document.md", "---\ncaption-numbering: 'document'\n---\n")
    in_body = md("body.md", "본문\n\ncaption-numbering: document\n")
    unknown = md("unknown.md", "---\ncaption-numbering: section\n---\n")

    assert caption_numbering([plain]) == "chapter"
    assert caption_numbering([chapter]) == "chapter"
    # Quoted as YAML may be; a file that does not say leaves it to the one that does.
    assert caption_numbering([plain, document, document]) == "document"
    # Only the front matter says it, not the body.
    assert caption_numbering([in_body]) == "chapter"
    with pytest.raises(ConversionError, match=r"unknown\.md: caption-numbering .* section"):
        caption_numbering([unknown])
    # Files that disagree: a document has one way, so neither is chosen.
    with pytest.raises(ConversionError, match=r"chapter\.md 는 'chapter', .*document\.md 는 'document'"):
        caption_numbering([chapter, plain, document])
