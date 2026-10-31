"""Convert a Markdown file to HWPX using a template document."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

from hwpx import HwpxDocument
from hwpx.oxml import Style
from hwpx.oxml.hyperlink_form import hyperlink_char_pr
from hwpx.tools.toc_author import add_native_toc, ensure_body_styles_not_collected, outline_heading_paragraphs
from markdown_it.tree import SyntaxTreeNode
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

from md2hwpx.images import MISSING_IMAGE, ImageError, length, picture_data, picture_size
from md2hwpx.parser import PARSER, custom_style
from md2hwpx.tables import CELL_MARGIN_LR, CELL_MARGIN_TB, column_widths, fit_rows
# YAML front matter: a "---" first line (after an optional BOM) up to a "---" or "..." line.
_FRONT_MATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)
# The front matter key that says how captions are numbered (see caption_numbering), and its values.
CAPTION_NUMBERING = "caption-numbering"
CAPTION_NUMBERINGS = ("chapter", "document")
_CAPTION_NUMBERING = re.compile(rf"^{CAPTION_NUMBERING}:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
# "[표] ..." / "[그림] ..." right before a table or code block / image is its caption,
# written as "[표 N] ..." / "[그림 N] ..." numbered in document order.
_CAPTION = re.compile(r"^\[(표|그림)\][ \t]*")
# Lines of their own in a paragraph: table of contents, page break as pandoc writes it, number restart,
# page hiding and blank page markers.
_MARKERS = {
    # "[TOC]", or "[TOC 그림 표 제목]": the lists to make, in that order. "링크없음" leaves out the links.
    "toc": re.compile(r"\[TOC((?:[ \t]+(?:제목|표|그림|수식|링크없음))*)[ \t]*\]", re.IGNORECASE),
    "page_break": re.compile(r"\\(?:newpage|pagebreak)[ \t]*"),
    # "[새 번호 쪽 표 그림 수식]": those numbers start over at 1 from the next paragraph (Hancom's 새 번호로 시작).
    "restart": re.compile(r"\[새 번호((?:[ \t]+(?:쪽|표|그림|수식))+)[ \t]*\]"),
    # "[감추기]": the next paragraph's page shows no page number, header or footer (Hancom's 감추기).
    "hide_page": re.compile(r"\[감추기\]"),
    # "[여백면]" or "[여백면] 문구": a page kept as margin, with that text centred on it.
    "blank_page": re.compile(r"\[\s*여백면\s*\]\s*(.*)"),
}
_MARKS = {"strong": "bold", "em": "italic", "s": "strike"}
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HP_P = f"{_HP}p"
# Table cell style "text-align:..." -> paragraph alignment; no style keeps the default.
_ALIGN = {"text-align:left": "LEFT", "text-align:center": "CENTER", "text-align:right": "RIGHT"}
# The template that comes with the package: used when no other is given.
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "template.hwpx"
HEADING_STYLE = "개요 {}"  # .format(level)
BODY_STYLE = "본문"
PLAIN_STYLE = "바탕글"
CAPTION_STYLE = "표그림 제목"
HEADER_FILL = "#D9D9D9"
TABLE_BORDER = {"border_color": "#000000", "border_width": "0.12 mm"}  # the template's table border
TABLE_SIDES = ["LEFT", "RIGHT", "TOP", "BOTTOM"]
LIST_MAX_LEVEL = 3  # deeper items are kept at this level
LIST_INDENT = 1000  # HWPUNIT added to the left margin per list level, as the template's 개요 styles step
QUOTE_INDENT = 1000  # HWPUNIT added to the left margin per quote depth
QUOTE_BAR = {"border_color": "#A6A6A6", "border_width": "0.7 mm"}  # left bar of a quote
QUOTE_BAR_GAP = 850  # HWPUNIT between the quote bar and the text
RULE_LINE = {"border_color": "#808080", "border_width": "0.12 mm"}  # thematic break
TOC_TITLE = "차례"
TOC_LEVEL = 3  # heading levels listed, as the template's 차례 1-3 styles
# The TOC field's ContentsMake bits for each list, as Hancom writes them with only that list chosen.
# A plain [TOC] keeps python-hwpx's 31, all of them in one field.
TOC_KINDS = {"제목": 17, "표": 2, "그림": 4, "수식": 8}
TOC_NO_LINKS = "링크없음"  # in the [TOC] marker: entries are not hyperlinks to what they list
_TOC_MAKE = re.compile(r"ContentsMake:uint:\d+")
# Hancom's running numbers (hp:newNum / hp:autoNum numType) a "[새 번호]" line can start over.
NUMBERS = {"쪽": "PAGE", "표": "TABLE", "그림": "PICTURE", "수식": "EQUATION"}
CAPTION_RESERVE = 2500  # HWPUNIT kept below a picture for its caption: gap 850 + one 10pt line at 160%
# A picture is laid out as a character in its paragraph, so it follows that paragraph's alignment: one
# narrower than the body is centred rather than left at the margin.
PICTURE_REDUCED_ALIGN = "CENTER"
OBJECT_SPACING = 1400  # HWPUNIT outside a table, code block or picture, above and below: about one 10pt line
CODE_FONT = "D2Coding"
CODE_SIZE = 9  # pt
CODE_LINE_SPACING = 140  # %
# One line of code as Hancom draws it: 9pt at 140% spacing, 1260 HWPUNIT.
CODE_LINE_HEIGHT = CODE_SIZE * 100 * CODE_LINE_SPACING // 100
CODE_HIGHLIGHT_STYLE = "default"  # Pygments style
# A blank page's wording: 11pt, in the body font, one line at 160% spacing.
BLANK_PAGE_FONT_SIZE = 11  # pt
BLANK_PAGE_LINE_SPACING = 160  # %
BLANK_PAGE_MARGIN = {"left": 5660, "right": 5660, "top": 1415, "bottom": 1415}  # 20 mm sides, 5 mm above and below


def _line_metrics(doc: HwpxDocument, style: Style) -> tuple[int, int]:
    """(size in pt, one line's height in HWPUNIT) of *style*.

    Read from the template rather than written here: a table's rows are sized from it, so the template's
    own size and line spacing have to drive that, or a change to them would leave the rows wrong.
    """
    size = int(doc.styles.char_property(style.char_pr_id_ref).attributes["height"]) // 100
    spacing = doc.styles.paragraph_property(style.para_pr_id_ref).line_spacing.value
    return size, size * 100 * spacing // 100


class Run(NamedTuple):
    """Inline text with one look, or a footnote reference (*note*, with no text)."""

    text: str
    marks: frozenset[str] = frozenset()  # bold, italic, strike, code
    url: str | None = None  # link target
    note: int | None = None  # footnote id


class _Piece(NamedTuple):
    """Part of a paragraph: text "runs", an "image", or a marker line (a key of _MARKERS)."""

    kind: str
    runs: list[Run] | None = None
    image: SyntaxTreeNode | None = None
    text: str = ""  # a blank page's wording, or the TOC or restart marker's words (see _MARKERS)


class _Caption(NamedTuple):
    """A numbered caption waiting for the table, code block or picture it goes on."""

    runs: list[Run]  # "[표 2-1] " and the title
    before: str  # the text before the number, "[표 2-"
    number: int  # the object's count, which Hancom's own number (see _auto_number) starts from
    num_type: str  # that number's numType (a NUMBERS value)


@dataclass
class _PageStart:
    """What a paragraph does about its page: start a new one, start numbers over, hide the page number."""

    page_break: bool = False
    restarts: set[str] = field(default_factory=set)  # numTypes (NUMBERS values) that start over at 1 there
    hide: bool = False  # the page shows no page number, header or footer

    def apply(self, paragraph) -> None:
        if self.page_break:
            paragraph.element.set("pageBreak", "1")
        _new_numbers(paragraph, self.restarts)
        if self.hide:
            _hide_page(paragraph)


class ConversionError(Exception):
    """The Markdown cannot be converted (its front matter says what cannot be done), or the converted
    document failed HWPX validation, so it was not saved."""


def _runs(nodes: Iterable[SyntaxTreeNode], marks: frozenset[str] = frozenset(), url: str | None = None) -> list[Run]:
    """The runs of inline *nodes*. An image here (in a heading or table cell) gives its alt text."""
    runs: list[Run] = []
    for node in nodes:
        if node.type == "text":
            runs.append(Run(node.content, marks, url))
        elif node.type == "softbreak":
            runs.append(Run(" ", marks, url))
        elif node.type == "hardbreak":
            runs.append(Run("\n", marks, url))
        elif node.type == "code_inline":
            runs.append(Run(node.content, marks | {"code"}, url))
        elif node.type in _MARKS:
            runs += _runs(node.children, marks | {_MARKS[node.type]}, url)
        elif node.type == "link":
            runs += _runs(node.children, marks, node.attrs["href"])
        elif node.type == "image":
            runs += _runs(node.children, marks, url)
        elif node.type == "footnote_ref":
            runs.append(Run("", marks, url, note=node.meta["id"]))
    return runs


def _plain(runs: list[Run]) -> str:
    return "".join(run.text for run in runs)


def _is_blank(run: Run) -> bool:
    return run.note is None and not run.text.strip()


def _strip(runs: list[Run]) -> list[Run]:
    """*runs* without the white space at their start and end."""
    runs = list(runs)
    while runs and _is_blank(runs[0]):
        runs.pop(0)
    while runs and _is_blank(runs[-1]):
        runs.pop()
    if runs:
        runs[0] = runs[0]._replace(text=runs[0].text.lstrip())
        runs[-1] = runs[-1]._replace(text=runs[-1].text.rstrip())
    return runs


def _blank_page_width(text: str, size: int) -> int:
    """The wording's width for a blank page, in HWPUNIT.

    A character the size of the font itself (1pt = 100 HWPUNIT), a space and other narrow ones half of
    that, and one more character of room. A table sized to the text exactly wraps its last character as
    soon as the face draws a hair wider than the count, which is how a one-line wording came out as two;
    the spare character absorbs it.
    """
    full = size * 100
    width = sum(full if ord(char) > 0x2000 else full // 2 for char in text)
    return max(width, full) + full


def _space_above_below(obj) -> None:
    """Keep about a line of space above and below the table or picture *obj*, none at its sides."""
    margin = obj.element.find(f"{_HP}outMargin")
    margin.set("top", str(OBJECT_SPACING))
    margin.set("bottom", str(OBJECT_SPACING))


def _highlight(code: list[str], lang: str) -> list[list[tuple[str, dict]]]:
    """Split *code* into per-line (text, Pygments token style) runs."""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False, ensurenl=False)
    style = get_style_by_name(CODE_HIGHLIGHT_STYLE)
    lines: list[list[tuple[str, dict]]] = [[]]
    # The shell session lexers split their input by lines ending in "\n", so a final line without one
    # (as code blocks have) is left unhighlighted. The trailing newline's own empty line is dropped below.
    for token, value in lexer.get_tokens("\n".join(code) + "\n"):
        # A style has no entry for every token the lexers make (Generic.Prompt.VirtualEnv, say):
        # take the nearest parent it does have, as Pygments' own formatters do.
        while token.parent is not None and not style.styles_token(token):
            token = token.parent
        for k, part in enumerate(value.split("\n")):
            if k:
                lines.append([])
            if part:
                lines[-1].append((part, style.style_for_token(token)))
    return lines[:-1]


def _block_kind(node: SyntaxTreeNode | None) -> str | None:
    """The block's type, or "image" for a paragraph that starts with an image (a caption may precede it)."""
    if node is None:
        return None
    if node.type == "paragraph":
        first = next((c for c in node.children[0].children if not (c.type == "text" and not c.content.strip())), None)
        if first is not None and first.type == "image":
            return "image"
    return node.type


def _pieces(inline: SyntaxTreeNode) -> list[_Piece]:
    """Split a paragraph's inline content into pieces: each image, and each line holding only
    a marker, stands apart; the text between them is a "runs" piece (blank ones are dropped)."""
    pieces: list[_Piece] = []
    runs: list[Run] = []

    def flush() -> None:
        if not all(_is_blank(run) for run in runs):
            pieces.append(_Piece("runs", _strip(runs)))
        runs.clear()

    lines: list[list[SyntaxTreeNode]] = [[]]
    for child in inline.children:
        if child.type == "softbreak":
            lines.append([])
        else:
            lines[-1].append(child)
    for line in lines:
        text = line[0].content.strip() if len(line) == 1 and line[0].type == "text" else ""
        found = next(((name, m) for name, pattern in _MARKERS.items() if (m := pattern.fullmatch(text))), None)
        if found:
            flush()
            marker, match = found
            # The blank page marker carries a wording, the TOC and restart markers their words, in their one group.
            pieces.append(_Piece(marker, text=match.group(1).strip() if match.groups() else ""))
            continue
        if runs:
            runs.append(Run(" "))  # the line break between two lines of text
        for child in line:
            if child.type == "image":
                flush()
                pieces.append(_Piece("image", image=child))
            else:
                runs += _runs([child])
    flush()
    return pieces


def _paragraph_styles(doc: HwpxDocument) -> dict[str, Style]:
    """The document's paragraph styles by name: what a custom-style div can name."""
    return {style.name: style for style in doc.styles.values() if style.type == "PARA"}


class _Writer:
    """Writes the block tree of a Markdown document into an HWPX document."""

    def __init__(self, doc: HwpxDocument, base_dir: Path, root: SyntaxTreeNode, by_chapter: bool):
        self.doc = doc
        self.base_dir = base_dir
        self.by_chapter = by_chapter  # captions are numbered within each chapter, else through the document
        # Styles are resolved once: python-hwpx looks a style name up by walking all of header.xml,
        # which grows with every character and paragraph shape the conversion adds.
        self.body_style = doc.styles.resolve(BODY_STYLE)
        self.plain_style = doc.styles.resolve(PLAIN_STYLE)
        self.caption_style = doc.styles.resolve(CAPTION_STYLE)
        self.heading_styles: dict[int, Style] = {}
        self.paragraph_styles = _paragraph_styles(doc)
        self.text_style = self.body_style  # of text paragraphs: 본문, or the custom-style div's they are in
        # add_table / add_picture take only the style id from style=, so pass its paragraph shape too.
        self.body = {"style": self.body_style, "para_pr_id_ref": self.body_style.para_pr_id_ref}
        # A picture is laid out as a character in its paragraph, so it takes the plain 바탕글 style:
        # 본문 indents its first line, which would push the picture in from the left.
        self.picture_body = {"style": self.plain_style, "para_pr_id_ref": self.plain_style.para_pr_id_ref}
        # The template's 바탕글 size and line height: a table's columns and rows are sized from them.
        self.plain_size, self.plain_line_height = _line_metrics(doc, self.plain_style)
        # The page body a 본문 paragraph can fill.
        margin = doc.styles.paragraph_property(self.body_style.para_pr_id_ref).margin
        size, page = doc.page.size(), doc.page.margins()
        self.body_left = int(margin.left)
        self.body_width = size.width - page.left - page.right - page.gutter - int(margin.left) - int(margin.right)
        self.body_height = size.height - page.top - page.bottom - page.header - page.footer
        # python-hwpx compares each requested shape with every one in the document, and lists all
        # paragraphs to find one by index; with hundreds of pages that adds up, so remember results.
        self._formats: dict[tuple, str] = {}
        self._list_headings: dict[tuple, dict] = {}  # (bullet level) / (numbering, level) -> hh:heading
        self._section = doc.sections[-1]  # where new paragraphs go
        self._paragraphs_before = len(doc.paragraphs) - len(self._section.element.findall(_HP_P))
        # Footnote definitions by id, from the block the footnote plugin puts at the end.
        self.notes = {
            note.meta["id"]: note for block in root.children if block.type == "footnote_block" for note in block.children
        }
        self.caption: _Caption | None = None  # for the next table, code block or picture
        # Caption numbers count within the chapter: "표 2-1" is the first table of the second level 1 heading.
        self.chapter = 0  # the level 1 heading being written, 0 before the first one
        self.numbers = {"표": 0, "그림": 0}  # within that chapter, or since a "[새 번호 표]" / "[새 번호 그림]"
        self.toc_at: int | None = None  # paragraph index of the first [TOC] marker
        self.toc_kinds: list[str] = []  # the lists the [TOC] marker names, in order (none: all in one field)
        self.toc_links = True  # entries are hyperlinks, unless the [TOC] marker says 링크없음
        self.page_starts: dict[int, _PageStart] = {}  # paragraph index -> what it does about its page
        self.toc_start = _PageStart()  # what the [TOC] marker's position asked for, done by the TOC's title
        # Paragraphs already there when the conversion starts (see has_content).
        self._first_content = self.paragraph_count()

    # --- shapes -----------------------------------------------------------------------------

    def _ensure(self, kind: str, **request) -> str:
        key = (kind, repr(sorted(request.items())))
        if key not in self._formats:
            ensure = {
                "para": self.doc.styles.ensure_paragraph_format,
                "char": self.doc.styles.ensure_run,
                "border": self.doc.styles.ensure_border_fill,
            }[kind]
            self._formats[key] = ensure(**request)
        return self._formats[key]

    def para_format(self, base: str | None, **changes) -> str:
        """The paragraph shape *base* with *changes* (doc.styles.ensure_paragraph_format), remembered."""
        return self._ensure("para", base_para_pr_id=base, **changes)

    def char_format(self, base: str | None, **changes) -> str:
        """The character shape *base* with *changes* (doc.styles.ensure_run), remembered."""
        return self._ensure("char", base_char_pr_id=base, **changes)

    def border_fill(self, **request) -> str:
        """doc.styles.ensure_border_fill, remembered."""
        return self._ensure("border", **request)

    def paragraph_count(self) -> int:
        """len(doc.paragraphs), counted in the section XML instead of wrapping every paragraph."""
        return self._paragraphs_before + len(self._section.element.findall(_HP_P))

    def has_content(self) -> bool:
        """Whether anything is written yet. The [TOC] marker counts, though its paragraphs go in only in finish()."""
        return self.paragraph_count() > self._first_content or self.toc_at is not None

    def page_start(self) -> _PageStart:
        """What the next paragraph does about its page."""
        return self.page_starts.setdefault(self.paragraph_count(), _PageStart())

    def new_page(self) -> None:
        """Start a new page at the next paragraph, unless nothing is written yet: the first page is new."""
        if self.has_content():
            self.page_start().page_break = True

    # --- runs -------------------------------------------------------------------------------

    def write_runs(self, paragraph, runs: list[Run], footnotes: bool = True) -> None:
        """Rewrite *paragraph* (holding the plain text of *runs* in one run) with *runs*:
        a run per change of marks, links as hyperlinks, footnotes (dropped if not *footnotes*)."""
        merged: list[Run] = []
        for run in runs:
            if run.note is None and not run.text:
                continue  # markdown-it leaves empty text around emphasis
            if run.note is not None and not footnotes:
                continue
            last = merged[-1] if merged else None
            if last and last.note is None and run.note is None and (last.marks, last.url) == (run.marks, run.url):
                merged[-1] = last._replace(text=last.text + run.text)
            else:
                merged.append(run)
        if all(not run.marks and run.url is None and run.note is None for run in merged):
            return
        base = paragraph.char_pr_id_ref
        char_prs: dict[frozenset[str], str | None] = {frozenset(): base}

        def char_pr(marks: frozenset[str]) -> str | None:
            if marks not in char_prs:
                # None keeps the base's bold / italic / strike, so a bold header cell stays bold.
                char_prs[marks] = self.char_format(
                    base,
                    bold=True if "bold" in marks else None,
                    italic=True if "italic" in marks else None,
                    strike=True if "strike" in marks else None,
                    font=CODE_FONT if "code" in marks else None,
                )
            return char_prs[marks]

        paragraph.clear_text()
        for run in merged:
            if run.note is not None:
                self.add_footnote(paragraph, run.note)
            elif run.url is None:
                paragraph.add_run(run.text, char_pr_id_ref=char_pr(run.marks))
            else:
                # Hancom's link look (blue, underlined) on top of the run's own marks.
                link_pr = hyperlink_char_pr(paragraph.section, None, char_pr(run.marks))
                paragraph.add_hyperlink(run.url, run.text, char_pr_id_ref=link_pr)

    def add_footnote(self, paragraph, note_id: int) -> None:
        """A Hancom footnote at the end of *paragraph*; the definition's paragraphs become lines."""
        runs: list[Run] = []
        for block in self.notes[note_id].children:
            if block.type == "paragraph":
                if runs:
                    runs.append(Run("\n"))
                runs += _runs(block.children[0].children)
        runs = _strip(runs)
        note = self.doc.notes.add_footnote(_plain(runs), paragraph)
        self.write_runs(note.body_paragraph, runs, footnotes=False)

    def plain(self, paragraph, size: int | None = None, with_runs: bool = True) -> None:
        """Put *paragraph* on 바탕글 (휴먼명조), the style table cells and pictures are set in.

        The 본문 style indents its first line, which would shift a cell's text in from its left edge and
        push a picture in from the left. *size* overrides the style's own size, for the blank page's
        wording. *with_runs* brings the runs already in *paragraph* along, for a paragraph whose text came
        from set_text; a paragraph holding a table instead keeps its own.
        """
        paragraph.style_id_ref = self.plain_style.id
        paragraph.para_pr_id_ref = self.plain_style.para_pr_id_ref
        if not with_runs:
            return
        paragraph.char_pr_id_ref = (
            self.plain_style.char_pr_id_ref if size is None
            else self.char_format(self.plain_style.char_pr_id_ref, size=size)
        )
        for run in paragraph.runs:
            run.char_pr_id_ref = paragraph.char_pr_id_ref

    def text_paragraph(self, runs: list[Run], quote: int, indent: int):
        paragraph = self.doc.add_paragraph(_plain(runs), style=self.text_style)
        self.write_runs(paragraph, runs)
        self.place(paragraph, quote, indent)
        return paragraph

    def place(self, paragraph, quote: int, indent: int, bar: bool = True) -> None:
        """Indent *paragraph* by its quote depth and *indent*, with the quote bar on the left."""
        if not quote and not indent:
            return
        layout: dict = {"margins": {"left": self.body_left + quote * QUOTE_INDENT + indent}}
        if quote and bar:
            line = self.border_fill(active_borders=["LEFT"], **QUOTE_BAR)
            # connect: one bar along consecutive quote paragraphs
            layout["border"] = {"borderFillIDRef": line, "offsetLeft": QUOTE_BAR_GAP, "connect": "1"}
        paragraph.para_pr_id_ref = self.para_format(paragraph.para_pr_id_ref, **layout)

    def set_caption(self, obj, side: str) -> None:
        if self.caption is None:
            # Hancom numbers every table and picture, captioned or not: keep this one out of the count.
            obj.element.set("numberingType", "NONE")
            return
        style, caption = self.caption_style, self.caption
        made = obj.set_caption(_plain(caption.runs), side=side, char_pr_id_ref=style.char_pr_id_ref)
        for paragraph in made.paragraphs:
            paragraph.style_id_ref = style.id
            paragraph.para_pr_id_ref = style.para_pr_id_ref
            self.write_runs(paragraph, caption.runs, footnotes=False)
        _auto_number(made.paragraphs[0], caption.before, caption.number, caption.num_type)
        self.caption = None

    def take_caption(self, runs: list[Run], following: str | None) -> bool:
        """If *runs* is a "[표] ..." / "[그림] ..." line and *following* (the kind of what comes
        next: a block type or "image") is what it captions, keep it as the next caption."""
        first = runs[0]
        match = _CAPTION.match(first.text) if first == Run(first.text) else None
        if match is None:
            return False
        kind = match.group(1)
        captioned = ("table", "fence", "code_block") if kind == "표" else ("image",)
        if following not in captioned:
            return False
        self.number_caption(kind, [first._replace(text=first.text[match.end() :]), *runs[1:]])
        return True

    def number_caption(self, kind: str, title: list[Run]) -> None:
        """Keep "[표 2-1] *title*" / "[그림 2-1] *title*" as the next caption: the number is the chapter
        (the level 1 heading it is under) and, after it, the object's own count within that chapter.
        Numbered through the document (not by_chapter), it is the count alone: "[표 7] *title*".

        The count becomes Hancom's own number (see _auto_number), which starts over where the count does.
        """
        self.numbers[kind] += 1
        number = self.numbers[kind]
        before = f"[{kind} {self.chapter}-" if self.chapter and self.by_chapter else f"[{kind} "
        self.caption = _Caption([Run(f"{before}{number}] "), *title], before, number, NUMBERS[kind])

    def restart(self, names: Iterable[str]) -> None:
        """Start the numbers *names* (keys of NUMBERS) over at 1 from the next paragraph."""
        for name in names:
            self.page_start().restarts.add(NUMBERS[name])
            if name in self.numbers:
                self.numbers[name] = 0

    def add_table(self, rows: int, columns: int, quote: int, indent: int):
        """A new table (a code block is one too) that runs on to the next pages, placed like a paragraph."""
        table = self.doc.add_table(rows, columns, **self.body)
        # Hancom never breaks a table laid out as a character (python-hwpx's default): rows past the page
        # are left off. Placed from the paragraph, not the column, it keeps the paragraph's indent.
        table.set_treat_as_char(False)
        table.element.find(f"{_HP}pos").set("horzRelTo", "PARA")
        _space_above_below(table)
        # Cell padding: a cell without hasMargin uses the table's inMargin, whatever the template has there.
        # The height each row needs for its text is set by the caller, once the text is in (see fit_rows).
        padding = table.element.find(f"{_HP}inMargin")
        for side, value in (("left", CELL_MARGIN_LR), ("right", CELL_MARGIN_LR),
                            ("top", CELL_MARGIN_TB), ("bottom", CELL_MARGIN_TB)):
            padding.set(side, str(value))
        self.place(table.paragraph, quote, indent)
        return table

    # --- blocks -----------------------------------------------------------------------------

    def heading(self, node: SyntaxTreeNode) -> None:
        """Write a heading, with the caption number reset its level asks for. Whether a heading starts a page
        is the template's to say, in its 개요 style's paragraph shape."""
        runs = _runs(node.children[0].children)
        depth = int(node.tag[1])
        if depth not in self.heading_styles:
            self.heading_styles[depth] = self.doc.styles.resolve(HEADING_STYLE.format(depth))
        has_content = self.has_content()  # before the empty paragraph below adds to it
        if depth > 1:
            # An empty 본문 paragraph before a sub-heading: the template's 개요 styles carry no
            # space above, so the gap from what came before is that paragraph's own line.
            self.doc.add_paragraph(style=self.body_style)
        if depth == 1:
            self.chapter += 1
            if self.by_chapter:
                # A new chapter starts the caption numbers over: 표 2-1 follows 표 1-4.
                self.restart(self.numbers)
        heading = self.doc.add_heading(_plain(runs), level=depth, style=self.heading_styles[depth])
        if not has_content:
            # The first page is new already: a style that breaks the page before the heading would leave it
            # empty, as the template's own first paragraph comes before.
            heading.para_pr_id_ref = self.para_format(heading.para_pr_id_ref, break_setting={"page_break_before": False})
        self.write_runs(heading, runs)

    def blocks(self, nodes: list[SyntaxTreeNode], quote: int = 0, indent: int = 0, level: int = 0,
               numbering: str | None = None) -> None:
        """Write *nodes*, inside *quote* quotes and *level* list items placed at *indent*.

        *numbering* is the numbering id of the numbered list item holding them, if any.
        """
        content = indent + min(level, LIST_MAX_LEVEL) * LIST_INDENT
        for k, node in enumerate(nodes):
            following = nodes[k + 1] if k + 1 < len(nodes) else None
            if node.type == "heading":
                self.heading(node)
            elif node.type == "paragraph":
                self.pieces(_pieces(node.children[0]), _block_kind(following), quote, content)
            elif node.type in ("fence", "code_block"):
                lang = node.info.split()[0] if node.info.strip() else ""
                self.code_block(node.content.split("\n")[:-1], lang, quote, content)
            elif node.type == "table":
                self.table(node, quote, content)
            elif node.type == "hr":
                self.rule(quote, content)
            elif node.type in ("bullet_list", "ordered_list"):
                self.list_items(node, quote, indent, level + 1, numbering)
            elif node.type == "blockquote":
                self.blocks(node.children, quote + 1, indent, level)
            elif node.type == "container_div":
                # Its text paragraphs take the style it names; one the template lacks leaves them 본문
                # (the caller warns about it, see unknown_styles). Inside another div, this one's style wins.
                outer = self.text_style
                self.text_style = self.paragraph_styles.get(custom_style(node.info), self.body_style)
                self.blocks(node.children, quote, indent, level, numbering)
                self.text_style = outer

    def pieces(self, pieces: list[_Piece], following: str | None, quote: int, indent: int) -> None:
        """A paragraph's pieces become text paragraphs, pictures, captions, the TOC position and page breaks.

        *following* is the kind of the block after the paragraph (see _block_kind).
        """
        for k, piece in enumerate(pieces):
            if piece.kind == "toc":
                if self.toc_at is None:
                    self.toc_at = self.paragraph_count()
                    words = piece.text.split()
                    self.toc_kinds = [w for w in dict.fromkeys(words) if w != TOC_NO_LINKS]
                    self.toc_links = TOC_NO_LINKS not in words
                    # A page break, restart or hiding asked for here goes on the TOC's title.
                    self.toc_start = self.page_starts.pop(self.toc_at, _PageStart())
            elif piece.kind == "page_break":
                self.new_page()
            elif piece.kind == "restart":
                self.restart(piece.text.split())
            elif piece.kind == "hide_page":
                self.page_start().hide = True
            elif piece.kind == "blank_page":
                self.blank_page(piece.text)
            elif piece.kind == "image":
                self.picture(piece.image, quote, indent)
            else:
                # What a caption line would caption: the next piece of this paragraph, or the next block.
                next_kind = pieces[k + 1].kind if k + 1 < len(pieces) else following
                if not self.take_caption(piece.runs, next_kind):
                    self.text_paragraph(piece.runs, quote, indent)

    def picture(self, node: SyntaxTreeNode, quote: int, indent: int) -> None:
        image_path = self.base_dir / unquote(node.attrs["src"])
        try:
            data, image_format = picture_data(image_path)
        except ImageError:  # the caller warns about it (unusable_images)
            image_path = MISSING_IMAGE
            data, image_format = picture_data(image_path)
        if self.caption is None:  # without a "[그림] ..." line, the alt text (if any) is the caption
            alt = _strip(_runs(node.children))
            if alt:
                self.number_caption("그림", alt)
        max_height = self.body_height - 2 * OBJECT_SPACING - (CAPTION_RESERVE if self.caption is not None else 0)
        width, height = picture_size(
            image_path,
            data,
            self.body_width,
            max_height,
            length(node.attrs.get("width"), self.body_width),
            length(node.attrs.get("height"), self.body_height),
        ) or (None, None)
        picture = self.doc.add_picture(data, image_format, width=width, height=height, **self.picture_body)
        _space_above_below(picture)
        self.place(picture.paragraph, quote, indent)
        # A picture narrower than the body sits at the left margin and leaves a gap at the right; centred,
        # the gap is shared. The picture is laid out as a character, so the paragraph's own alignment is
        # what moves it. A picture that fills the body has nothing to centre, and keeps its style.
        if width is not None and width < self.body_width - indent:
            picture.paragraph.para_pr_id_ref = self.para_format(
                picture.paragraph.para_pr_id_ref, alignment=PICTURE_REDUCED_ALIGN
            )
        self.set_caption(picture, "BOTTOM")

    def blank_page(self, text: str) -> None:
        """A page kept as margin: a 1x1 table just big enough for *text*, centred on its own page.

        The table is not laid out as a character — that would follow the text flow and ignore the centring —
        and carries numberingType NONE so Hancom's table of contents leaves it out. The next block starts on
        the page after it, so the page break goes on the next paragraph (see the piece after this one).
        """
        line = BLANK_PAGE_FONT_SIZE * 100 * BLANK_PAGE_LINE_SPACING // 100  # the font at 160% line spacing
        margin = BLANK_PAGE_MARGIN
        width = _blank_page_width(text, BLANK_PAGE_FONT_SIZE) + margin["left"] + margin["right"]
        height = line + margin["top"] + margin["bottom"]
        self.new_page()  # on a page of its own: the table's paragraph comes next
        table = self.doc.add_table(1, 1)
        size = table.element.find(f"{_HP}sz")
        size.set("width", str(width))
        size.set("height", str(height))
        cell = table.cell(0, 0)
        cell.set_size(width=width, height=height)
        cell.set_margins(**margin)
        cell.text = text
        paragraph = cell.paragraphs[0]
        # 바탕글 (휴먼명조), at the blank page's own size: set_size and _blank_page_width above are measured
        # at it, so the box holds the wording on one line.
        self.plain(paragraph, size=BLANK_PAGE_FONT_SIZE)
        table.element.set("numberingType", "NONE")
        # Centred on the page: an inline table would follow the paragraph's flow instead.
        position = table.element.find(f"{_HP}pos")
        position.set("treatAsChar", "0")
        position.set("vertRelTo", "PAGE")
        position.set("vertAlign", "CENTER")
        position.set("horzRelTo", "PAGE")
        position.set("horzAlign", "CENTER")
        _space_above_below(table)
        self.plain(table.paragraph, with_runs=False)  # holds the table, not text: its run is the table's own
        # The next block — a piece of this paragraph or the following one — starts on the page after it.
        # A break with nothing after it is dropped in finish().
        self.page_start().page_break = True

    def table(self, node: SyntaxTreeNode, quote: int, indent: int) -> None:
        rows = [tr for section in node.children for tr in section.children]
        cells = [[_runs(cell.children[0].children) if cell.children else [] for cell in tr.children] for tr in rows]
        aligns = [_ALIGN.get(cell.attrs.get("style", "")) for cell in rows[0].children]
        table = self.add_table(len(cells), len(cells[0]), quote, indent)
        # The header row's cells ("header") come again at the top of each page the table runs on to.
        table.element.set("repeatHeader", "1")
        texts = [[_plain(runs) for runs in row] for row in cells]
        table.set_column_widths(column_widths(texts, self.body_width, self.plain_size))
        self.set_caption(table, "TOP")
        for r, row in enumerate(cells):
            for c, runs in enumerate(row):
                cell = table.cell(r, c)
                cell.text = _plain(runs)
                paragraph = cell.paragraphs[0]
                # 바탕글 (휴먼명조): a table's text is not body text, and the 본문 style's first-line
                # indent would shift every cell in from its left edge.
                self.plain(paragraph)
                if aligns[c]:
                    paragraph.para_pr_id_ref = self.para_format(paragraph.para_pr_id_ref, alignment=aligns[c])
                if r == 0:
                    cell.element.set("header", "1")
                    paragraph.char_pr_id_ref = self.char_format(paragraph.char_pr_id_ref, bold=True)
                    # Shaded, keeping the table's own border: set_cell_shading looks a border fill up by
                    # fill colour alone, and the template's #D9D9D9 one has no borders at all.
                    cell.element.set(
                        "borderFillIDRef",
                        self.border_fill(fill_color=HEADER_FILL, active_borders=TABLE_SIDES, **TABLE_BORDER),
                    )
                self.write_runs(paragraph, runs)
        fit_rows(table, self.doc, self.plain_line_height)

    def code_block(self, code: list[str], lang: str, quote: int, indent: int) -> None:
        table = self.add_table(1, 1, quote, indent)
        self.set_caption(table, "TOP")
        cell = table.cell(0, 0)
        cell.set_text("\n".join(code), split_paragraphs=True)

        def char_pr(base: str | None, color: str | None, bold: bool, italic: bool) -> str:
            return self.char_format(
                base, font=CODE_FONT, size=CODE_SIZE, color=color and f"#{color}", bold=bold, italic=italic
            )

        for paragraph, runs in zip(cell.paragraphs, _highlight(code, lang)):
            base = paragraph.char_pr_id_ref
            paragraph.clear_text()
            if not runs:
                paragraph.add_run("", char_pr_id_ref=char_pr(base, None, False, False))
            for text, token_style in runs:
                paragraph.add_run(
                    text,
                    char_pr_id_ref=char_pr(base, token_style["color"], token_style["bold"], token_style["italic"]),
                )
            paragraph.para_pr_id_ref = self.para_format(paragraph.para_pr_id_ref, line_spacing_percent=CODE_LINE_SPACING)
        # Every line of code, and any too wide for the cell that wraps, at the code's own line height.
        fit_rows(table, self.doc, CODE_LINE_HEIGHT)

    def rule(self, quote: int, indent: int) -> None:
        """A thematic break: an empty 본문 paragraph with a bottom border."""
        paragraph = self.doc.add_paragraph("", style=self.body_style)
        line = self.border_fill(active_borders=["BOTTOM"], **RULE_LINE)
        paragraph.para_pr_id_ref = self.para_format(paragraph.para_pr_id_ref, border={"borderFillIDRef": line})
        self.place(paragraph, quote, indent, bar=False)

    def list_items(self, node: SyntaxTreeNode, quote: int, indent: int, level: int, numbering: str | None) -> None:
        """Each item's first paragraph gets the bullet or number; its other blocks follow, indented.

        A numbered list starts its own numbering, or numbers on within the *numbering* of the
        numbered item holding it (Hancom numbers sub-levels within one definition).
        """
        list_level = min(level, LIST_MAX_LEVEL)
        start = int(node.attrs.get("start", 1))
        own: str | None = None  # this list's numbering id
        for k, item in enumerate(node.children):
            item_blocks = list(item.children)
            # The item text is the first text of its first paragraph; the rest of that paragraph
            # (pictures, more text) follows it like the item's other blocks.
            rest: list[_Piece] = []
            if item_blocks and item_blocks[0].type == "paragraph":
                rest = _pieces(item_blocks.pop(0).children[0])
            first = rest.pop(0).runs if rest and rest[0].kind == "runs" else []
            paragraph = self.doc.add_paragraph(_plain(first), style=self.body_style)
            self.write_runs(paragraph, first)
            if node.type == "bullet_list":
                self.list_format(paragraph, "bullet", list_level)
            else:
                own = self.list_format(paragraph, "number", list_level, own if k else numbering, start)
            self.place(paragraph, quote, indent + (list_level - 1) * LIST_INDENT)
            following = _block_kind(item_blocks[0] if item_blocks else None)
            self.pieces(rest, following, quote, indent + list_level * LIST_INDENT)
            self.blocks(item_blocks, quote, indent, level, own if node.type == "ordered_list" else None)

    def list_format(self, paragraph, kind: str, level: int, numbering: str | None = None, start: int = 1) -> str:
        """Give *paragraph*, the last one, a bullet or a number at *level* and return the numbering id.

        A number goes on in *numbering*, or starts a new numbering from *start* if it is None.
        apply_list_format lists every paragraph, so once a bullet level, or a level of a numbering,
        is set up, later items get the same heading directly.
        """
        if kind == "bullet" or numbering is not None:
            heading = self._list_headings.get(("bullet", level) if kind == "bullet" else (numbering, level))
            if heading is not None:
                paragraph.para_pr_id_ref = self.para_format(paragraph.para_pr_id_ref, heading=heading)
                return heading["idRef"]
        styles = self.doc.styles
        styles.apply_list_format(
            paragraph_index=self.paragraph_count() - 1, kind=kind, level=level,
            continue_list=numbering is not None, start=None if numbering is not None or start == 1 else start,
        )
        heading = styles.paragraph_property(paragraph.para_pr_id_ref).heading
        if numbering is not None and str(heading.id_ref) != numbering:
            # continue_list goes on with the last numbered paragraph, which may sit in a
            # nested list: point the item back at its own list's numbering.
            paragraph.para_pr_id_ref = self.para_format(
                paragraph.para_pr_id_ref, heading={"type": "NUMBER", "idRef": numbering, "level": level - 1}
            )
            heading = styles.paragraph_property(paragraph.para_pr_id_ref).heading
        id_ref = str(heading.id_ref)
        self._list_headings[("bullet", level) if kind == "bullet" else (id_ref, level)] = {
            "type": heading.type, "idRef": id_ref, "level": heading.level,
        }
        return id_ref

    # --- document ---------------------------------------------------------------------------

    def finish(self) -> None:
        """Page breaks and the table of contents, which go in once all paragraphs are there."""
        # Before the TOC goes in, while the indexes still hold. A break with nothing after it is dropped.
        paragraphs = self.doc.paragraphs
        for index, start in self.page_starts.items():
            if index < len(paragraphs):
                start.apply(paragraphs[index])

        headings = [p for p, level, _ in outline_heading_paragraphs(self.doc) if level <= TOC_LEVEL]
        if self.toc_at is not None and headings:
            # Hancom's TOC update also collects 바탕글 paragraphs (e.g. table cells): move them to 본문 first.
            ensure_body_styles_not_collected(self.doc)
            # dirty: Hancom rebuilds the TOC, with real page numbers, when it opens the document.
            if not self.toc_kinds:
                add_native_toc(
                    self.doc, at_index=self.toc_at, title=TOC_TITLE, level=TOC_LEVEL, hyperlink=self.toc_links,
                    headings=headings, dirty=True,
                )
            # A field per list: Hancom orders the lists within one field itself. Each goes in at the
            # same place, so they are added last first.
            for kind in reversed(self.toc_kinds):
                made = add_native_toc(
                    self.doc, at_index=self.toc_at, title=f"<{kind} 차례>", level=TOC_LEVEL,
                    hyperlink=self.toc_links, headings=headings, dirty=True,
                )
                paragraphs = self.doc.paragraphs[self.toc_at : self.toc_at + 1 + made["entryCount"]]
                _set_toc_make(paragraphs[0], made["tocFieldId"], TOC_KINDS[kind])
                if kind != "제목":
                    # Its entries are the headings: drop them, Hancom lists the captions when it rebuilds.
                    for entry in paragraphs[1:]:
                        entry.element.getparent().remove(entry.element)
            self.toc_start.apply(self.doc.paragraphs[self.toc_at])


def _new_numbers(paragraph, num_types: Iterable[str]) -> None:
    """Start Hancom's running numbers *num_types* over at 1 from *paragraph* (hp:newNum)."""
    first_run = paragraph.element.find(f"{_HP}run")
    for num_type in sorted(num_types):
        ctrl = paragraph.add_new_num(number=1, kind=num_type)
        # Ahead of what the paragraph holds: a table in it is numbered after the restart.
        first_run.addprevious(ctrl.element.getparent())


def _hide_page(paragraph) -> None:
    """Hide the page number, header and footer on *paragraph*'s page only (hp:pageHiding)."""
    first_run = paragraph.element.find(f"{_HP}run")
    ctrl = paragraph.add_page_hiding(header=True, footer=True, page_num=True)
    first_run.addprevious(ctrl.element.getparent())  # with the paragraph's other page controls, up front


def _auto_number(paragraph, before: str, number: int, num_type: str) -> None:
    """Make *number*, right after *before* at the start of *paragraph*, Hancom's own running number
    (hp:autoNum *num_type*), as Hancom writes a caption's number; *number* is its cached value."""
    text = paragraph.element.find(f"{_HP}run/{_HP}t")
    rest = text.text[len(before) + len(str(number)) :]
    text.text = before
    ctrl = text.makeelement(f"{_HP}ctrl", {})
    auto = ctrl.makeelement(f"{_HP}autoNum", {"num": str(number), "numType": num_type})
    auto.append(auto.makeelement(f"{_HP}autoNumFormat", {
        "type": "DIGIT", "userChar": "", "prefixChar": "", "suffixChar": "", "supscript": "0",
    }))
    ctrl.append(auto)
    after = text.makeelement(f"{_HP}t", {})
    after.text = rest
    text.addnext(ctrl)
    ctrl.addnext(after)


def _set_toc_make(paragraph, field_id: str, make: int) -> None:
    """Set the ContentsMake bits (the lists it makes) of TOC field *field_id*, which opens in *paragraph*."""
    begin = next(b for b in paragraph.element.iter(f"{_HP}fieldBegin") if b.get("id") == field_id)
    command = next(p for p in begin.iter(f"{_HP}stringParam") if p.get("name") == "Command")
    params = _TOC_MAKE.sub(f"ContentsMake:uint:{make}", command.text.split(":", 3)[3])
    # "TableOfContents:set:N:...": Hancom writes N as the length of what follows less one.
    command.text = f"TableOfContents:set:{len(params) - 1}:{params}"


def _walk(node: SyntaxTreeNode) -> Iterable[SyntaxTreeNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _blank_front_matter(markdown: str) -> str:
    """*markdown* with its front matter as blank lines, so line numbers still count it."""
    return _FRONT_MATTER.sub(lambda m: "\n" * m.group(0).count("\n"), markdown, count=1)


def image_paths(markdown: str, base_dir: Path) -> list[tuple[int, Path]]:
    """(line number, path) of each image outside code blocks, its path taken from *base_dir*."""
    markdown = _blank_front_matter(markdown)
    lines = markdown.splitlines()
    images: list[tuple[int, Path]] = []
    for node in _walk(SyntaxTreeNode(PARSER.parse(markdown))):
        if node.type != "inline" or node.map is None:
            continue
        for image in (n for n in _walk(node) if n.type == "image"):
            src = image.attrs["src"]
            # The line of the paragraph that names the image.
            start, end = node.map
            raw = next((s for s in (src, unquote(src)) if any(s in line for line in lines[start:end])), None)
            number = next((i + 1 for i in range(start, end) if raw and raw in lines[i]), start + 1)
            images.append((number, base_dir / unquote(src)))
    return images


def unusable_images(markdown: str, base_dir: Path) -> list[tuple[int, Path, str]]:
    """(line number, path, why) of each image, outside code blocks, that cannot go in the HWPX
    (see picture_data): the conversion shows a placeholder instead."""
    unusable = []
    for number, path in image_paths(markdown, base_dir):
        try:
            picture_data(path)
        except ImageError as error:
            unusable.append((number, path, str(error)))
    return unusable


def paragraph_styles(template_path: Path) -> set[str]:
    """The names of the template's paragraph styles, which a custom-style div can name."""
    return set(_paragraph_styles(HwpxDocument.open(template_path)))


def unknown_styles(markdown: str, styles: Collection[str]) -> list[tuple[int, str]]:
    """(line number, name) of each custom-style div that names a paragraph style not in *styles*
    (see paragraph_styles): the conversion leaves its paragraphs 본문."""
    return [
        (token.map[0] + 1, name)
        for token in PARSER.parse(_blank_front_matter(markdown))
        if token.type == "container_div_open" and (name := custom_style(token.info)) not in styles
    ]


def read_markdown(md_path: Path) -> str:
    """Read *md_path* (UTF-8) without its front matter, which is metadata, not document content."""
    return _FRONT_MATTER.sub("", md_path.read_text(encoding="utf-8"), count=1)


def caption_numbering(md_paths: Iterable[Path]) -> str:
    """How the captions of the document made of *md_paths* are numbered, as their front matter says
    ("caption-numbering: ..."): "chapter" ([표 2-1], within each chapter) or "document" ([표 7]).

    A file that does not say leaves it to the others; if none does, it is "chapter". Raises ConversionError
    for any other value, or for files that say different ones: a document has one way.
    """
    said: dict[str, Path] = {}  # value -> the first file that says it
    for path in md_paths:
        front = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
        match = _CAPTION_NUMBERING.search(front[0]) if front else None
        if match is None:
            continue
        value = match[1].strip("\"'")
        if value not in CAPTION_NUMBERINGS:
            raise ConversionError(f"{path}: {CAPTION_NUMBERING} 는 chapter 또는 document 여야 합니다: {value}")
        said.setdefault(value, path)
    if len(said) > 1:
        (first, first_path), (other, other_path) = said.items()
        raise ConversionError(
            f"{CAPTION_NUMBERING} 가 파일마다 다릅니다: {first_path} 는 '{first}', {other_path} 는 '{other}'"
        )
    return next(iter(said), "chapter")


def convert(md_path: Path, template_path: Path, out_path: Path) -> None:
    convert_markdown(read_markdown(md_path), md_path.parent, template_path, out_path, caption_numbering([md_path]))


def convert_markdown(
    markdown: str, base_dir: Path, template_path: Path, out_path: Path, caption_numbering: str = "chapter"
) -> None:
    """Convert *markdown*, whose image paths are relative to *base_dir*, its captions numbered
    *caption_numbering* (see the function of that name)."""
    doc = HwpxDocument.open(template_path)
    root = SyntaxTreeNode(PARSER.parse(markdown))
    writer = _Writer(doc, base_dir, root, by_chapter=caption_numbering == "chapter")
    writer.blocks(root.children)
    writer.finish()

    report = doc.validate()
    if not report.ok:
        raise ConversionError(
            "변환한 문서가 HWPX 검증에 실패해 저장하지 않았습니다:\n" + "\n".join(str(issue) for issue in report.errors)
        )
    doc.save_to_path(out_path)
