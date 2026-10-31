"""The table of contents a [TOC] marker asks for: what its words ask for, the paragraphs it lists, and the
Hancom TOC fields made for it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from hwpx import HwpxDocument
from hwpx.oxml import Style
from hwpx.tools.toc_author import add_native_toc, outline_heading_paragraphs

_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
# The [TOC] marker's words (see pieces.MARKERS for the marker itself).
TOC_NO_LINKS = "링크없음"  # in the [TOC] marker: entries are not hyperlinks to what they list
TOC_OUTLINE = "개요"  # in the [TOC] marker: the 제목 list collects 개요 paragraphs, with its styles if any
_TOC_STYLES = re.compile(r'스타일="([^"]*)"')  # in the [TOC] marker: the styles the 제목 list collects
_TOC_WORD = re.compile(r'스타일="[^"]*"|\S+')
# The TOC fields.
TOC_HEADING = "차례"  # the title of a TOC of all lists in one field (a plain [TOC])
TOC_LEVEL = 3  # heading levels listed, as the template's 차례 1-3 styles
# The TOC field's ContentsMake bits for the 표, 그림 and 수식 lists, as Hancom writes them with only that list
# chosen. A plain [TOC] keeps python-hwpx's 31, all of them in one field.
TOC_KINDS = {"표": 2, "그림": 4, "수식": 8}
# The 제목 list's bits, as Hancom writes them: the list itself (1), and where it collects its entries from,
# 개요 paragraphs (16, "개요 문단으로 모으기") and the styles named in ContentsStyles (32, "스타일로 모으기"):
# 17 with only 개요 paragraphs.
TOC_TITLE_LIST, TOC_BY_OUTLINE, TOC_BY_STYLE = 1, 16, 32
_TOC_MAKE = re.compile(r"ContentsMake:uint:\d+")
_TOC_NO_STYLES = "ContentsStyles:wstring:0:"  # as python-hwpx writes the TOC command: no styles


class TocRequest(NamedTuple):
    """What a [TOC] marker asks for (see toc_request)."""

    kinds: list[str]  # the lists to make, in order: 제목, 표, 그림, 수식 (none: all in one field)
    styles: list[str]  # the styles the 제목 list collects
    outline: bool  # the 제목 list collects 개요 paragraphs
    links: bool  # entries are hyperlinks to what they list


def toc_request(words: str) -> TocRequest:
    """What the [TOC] marker's *words* ask for.

    제목, 개요 and 스타일="..." all name the 제목 list (at the first of them). It collects 개요 paragraphs
    unless styles are named; with them, the paragraphs of those styles, and 개요 ones too if 개요 is named.
    Each style is named once, from 스타일="A;B".
    """
    names = (name.strip() for match in _TOC_STYLES.finditer(words) for name in match[1].split(";"))
    styles = list(dict.fromkeys(name for name in names if name))
    split = _TOC_WORD.findall(words)
    kinds = ("제목" if word == TOC_OUTLINE or _TOC_STYLES.fullmatch(word) else word for word in split)
    return TocRequest(
        kinds=[kind for kind in dict.fromkeys(kinds) if kind != TOC_NO_LINKS],
        styles=styles,
        outline=TOC_OUTLINE in split or not styles,
        links=TOC_NO_LINKS not in split,
    )


def toc_entries(doc: HwpxDocument, request: TocRequest, styles: dict[str, Style]) -> list:
    """The paragraphs of *doc* the 제목 list *request* asks for collects, in document order: 개요 ones down to
    TOC_LEVEL (if it collects them) and those of its styles (looked up by name in *styles*). Hancom collects
    them again when it rebuilds the TOC."""
    outline = (
        {p.element.get("styleIDRef") for p, level, _ in outline_heading_paragraphs(doc) if level <= TOC_LEVEL}
        if request.outline else set()
    )
    styled = {str(styles[name].id) for name in request.styles if name in styles}
    return [p for p in doc.paragraphs if p.text.strip() and p.element.get("styleIDRef") in outline | styled]


def add_toc(doc: HwpxDocument, at_index: int, request: TocRequest, headings: list) -> None:
    """Put the TOC *request* asks for in *doc* at paragraph *at_index*, its 제목 list listing *headings*
    (see toc_entries)."""
    # dirty: Hancom rebuilds the TOC, with real page numbers, when it opens the document.
    if not request.kinds:
        add_native_toc(
            doc, at_index=at_index, title=TOC_HEADING, level=TOC_LEVEL, hyperlink=request.links,
            headings=headings, dirty=True,
        )
    # A field per list: Hancom orders the lists within one field itself. Each goes in at the
    # same place, so they are added last first.
    for kind in reversed(request.kinds):
        made = add_native_toc(
            doc, at_index=at_index, title=f"<{kind} 차례>", level=TOC_LEVEL,
            hyperlink=request.links, headings=headings, dirty=True,
        )
        paragraphs = doc.paragraphs[at_index : at_index + 1 + made["entryCount"]]
        if kind == "제목":
            make = TOC_TITLE_LIST | (TOC_BY_OUTLINE if request.outline else 0)
            make |= TOC_BY_STYLE if request.styles else 0
            _set_toc_command(paragraphs[0], made["tocFieldId"], make, request.styles)
        else:
            _set_toc_command(paragraphs[0], made["tocFieldId"], TOC_KINDS[kind])
            # Its entries are the headings: drop them, Hancom lists the captions when it rebuilds.
            for entry in paragraphs[1:]:
                entry.element.getparent().remove(entry.element)


def _set_toc_command(paragraph, field_id: str, make: int, styles: Iterable[str] = ()) -> None:
    """Set the ContentsMake bits (the lists it makes, and where from) and the ContentsStyles (the styles
    "스타일로 모으기" collects) of TOC field *field_id*, which opens in *paragraph*."""
    begin = next(b for b in paragraph.element.iter(f"{_HP}fieldBegin") if b.get("id") == field_id)
    command = next(p for p in begin.iter(f"{_HP}stringParam") if p.get("name") == "Command")
    params = _TOC_MAKE.sub(f"ContentsMake:uint:{make}", command.text.split(":", 3)[3])
    # As Hancom writes them: each name ends in ";", after the count of characters.
    style_list = "".join(f"{name};" for name in styles)
    params = params.replace(_TOC_NO_STYLES, f"ContentsStyles:wstring:{len(style_list)}:{style_list}", 1)
    # "TableOfContents:set:N:...": Hancom writes N as the length of what follows less one.
    command.text = f"TableOfContents:set:{len(params) - 1}:{params}"
