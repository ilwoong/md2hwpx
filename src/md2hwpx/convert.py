"""Convert a Markdown file to HWPX using a template document."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
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

from md2hwpx.diagram import LANGS
from md2hwpx.parser import PARSER
# YAML front matter: a "---" first line (after an optional BOM) up to a "---" or "..." line.
_FRONT_MATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)
# File names render_diagrams gives the PNGs it renders.
_DIAGRAM_IMAGE = re.compile(rf"^({'|'.join(LANGS)})-[0-9a-f]{{12}}\.png$")
# "[표] ..." / "[그림] ..." right before a table or code block / image is its caption,
# written as "[표 N] ..." / "[그림 N] ..." numbered in document order.
_CAPTION = re.compile(r"^\[(표|그림)\][ \t]*")
# Lines of their own in a paragraph: table of contents marker, and page break as pandoc writes it.
_MARKERS = {"toc": re.compile(r"\[TOC\]", re.IGNORECASE), "page_break": re.compile(r"\\(?:newpage|pagebreak)")}
_MARKS = {"strong": "bold", "em": "italic", "s": "strike"}
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HP_P = f"{_HP}p"
# An image's width / height attribute: percent of the page body, or millimetres.
_LENGTH = re.compile(r"(\d+(?:\.\d+)?)(%|mm)")
# Table cell style "text-align:..." -> paragraph alignment; no style keeps the default.
_ALIGN = {"text-align:left": "LEFT", "text-align:center": "CENTER", "text-align:right": "RIGHT"}
BODY_STYLE = "본문"
CAPTION_STYLE = "표그림제목"
HEADER_FILL = "#D9D9D9"
# A column is weighted by its longest cell (Hangul counts double), but never below this.
MIN_COLUMN_WEIGHT = 4
LIST_MAX_LEVEL = 3  # deeper items are kept at this level
LIST_INDENT = 1000  # HWPUNIT added to the left margin per list level, as the template's 개요 styles step
QUOTE_INDENT = 1000  # HWPUNIT added to the left margin per quote depth
QUOTE_BAR = {"border_color": "#A6A6A6", "border_width": "0.7 mm"}  # left bar of a quote
QUOTE_BAR_GAP = 850  # HWPUNIT between the quote bar and the text
RULE_LINE = {"border_color": "#808080", "border_width": "0.12 mm"}  # thematic break
TOC_TITLE = "차례"
TOC_LEVEL = 3  # heading levels listed, as the template's 차례 1-3 styles
HWPUNIT_PER_PIXEL = 75  # 7200 HWPUNIT per inch at 96 DPI
HWPUNIT_PER_MM = 7200 / 25.4  # 7200 HWPUNIT per inch
CAPTION_RESERVE = 2500  # HWPUNIT kept below a picture for its caption: gap 850 + one 10pt line at 160%
OBJECT_SPACING = 1400  # HWPUNIT outside a table, code block or picture, above and below: about one 10pt line
CODE_FONT = "D2Coding"
CODE_SIZE = 9  # pt
CODE_LINE_SPACING = 140  # %
CODE_HIGHLIGHT_STYLE = "default"  # Pygments style

class Run(NamedTuple):
    """Inline text with one look, or a footnote reference (*note*, with no text)."""

    text: str
    marks: frozenset[str] = frozenset()  # bold, italic, strike, code
    url: str | None = None  # link target
    note: int | None = None  # footnote id


class _Piece(NamedTuple):
    """Part of a paragraph: text "runs", an "image", or a "toc" / "page_break" marker line."""

    kind: str
    runs: list[Run] | None = None
    image: SyntaxTreeNode | None = None


class ConversionError(Exception):
    """The converted document failed HWPX validation, so it was not saved."""


def _image_pixels(data: bytes) -> tuple[int, int] | None:
    """(width, height) in pixels of a PNG, GIF or JPEG image, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            # Start-of-frame markers hold the size; C4 / C8 / CC are other tables.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
            i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    return None


def _length(value: str | None, whole: int) -> int | None:
    """A width / height attribute ("50%" of *whole*, or "80mm") in HWPUNIT, or None if absent, zero or neither."""
    match = _LENGTH.fullmatch(value or "")
    if match is None:
        return None
    number = float(match[1])
    length = round(whole * number / 100 if match[2] == "%" else number * HWPUNIT_PER_MM)
    return length or None


def _picture_size(
    image_path: Path, data: bytes, max_width: int, max_height: int, width: int | None = None, height: int | None = None
) -> tuple[int, int] | None:
    """Picture (width, height) in HWPUNIT within *max_width* x *max_height*, or None if the image size is unknown.

    The *width* / *height* asked for is used, the other side following the aspect ratio. Without either,
    a diagram is as wide as *max_width*; any other image keeps its size at 96 DPI.
    Any of them is scaled down, keeping its aspect ratio, to fit.
    """
    pixels = _image_pixels(data)
    if pixels is None or 0 in pixels:
        return None
    ratio = pixels[1] / pixels[0]
    if width is None and height is None:
        width = max_width if _DIAGRAM_IMAGE.match(image_path.name) else pixels[0] * HWPUNIT_PER_PIXEL
    if width is None:
        width = height / ratio
    if height is None:
        height = width * ratio
    scale = min(1.0, max_width / width, max_height / height)
    return round(width * scale), round(height * scale)


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


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


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
    for token, value in lexer.get_tokens("\n".join(code)):
        for k, part in enumerate(value.split("\n")):
            if k:
                lines.append([])
            if part:
                lines[-1].append((part, style.style_for_token(token)))
    return lines


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
        marker = next((name for name, pattern in _MARKERS.items() if pattern.fullmatch(text)), None)
        if marker:
            flush()
            pieces.append(_Piece(marker))
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


class _Writer:
    """Writes the block tree of a Markdown document into an HWPX document."""

    def __init__(self, doc: HwpxDocument, base_dir: Path, root: SyntaxTreeNode):
        self.doc = doc
        self.base_dir = base_dir
        # Styles are resolved once: python-hwpx looks a style name up by walking all of header.xml,
        # which grows with every character and paragraph shape the conversion adds.
        self.body_style = doc.styles.resolve(BODY_STYLE)
        self.caption_style = doc.styles.resolve(CAPTION_STYLE)
        self.heading_styles: dict[int, Style] = {}
        # add_table / add_picture take only the style id from style=, so pass its paragraph shape too.
        self.body = {"style": self.body_style, "para_pr_id_ref": self.body_style.para_pr_id_ref}
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
        self.caption: list[Run] | None = None  # for the next table, code block or picture
        self.numbers = {"표": 0, "그림": 0}
        self.toc_at: int | None = None  # paragraph index of the first [TOC] marker
        self.toc_page_break = False  # a page break right before the [TOC] marker
        self.page_breaks: list[int] = []  # indexes of the paragraphs that start a new page

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
            if last and last.note is None and run.note is None and last[1:] == run[1:]:
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

    def text_paragraph(self, runs: list[Run], quote: int, indent: int):
        paragraph = self.doc.add_paragraph(_plain(runs), style=self.body_style)
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
            return
        style = self.caption_style
        caption = obj.set_caption(_plain(self.caption), side=side, char_pr_id_ref=style.char_pr_id_ref)
        for paragraph in caption.paragraphs:
            paragraph.style_id_ref = style.id
            paragraph.para_pr_id_ref = style.para_pr_id_ref
            self.write_runs(paragraph, self.caption, footnotes=False)
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
        """Keep "[표 N] *title*" / "[그림 N] *title*" as the next caption, N counting each kind."""
        self.numbers[kind] += 1
        self.caption = [Run(f"[{kind} {self.numbers[kind]}] "), *title]

    def add_table(self, rows: int, columns: int, quote: int, indent: int):
        """A new table (a code block is one too) that runs on to the next pages, placed like a paragraph."""
        table = self.doc.add_table(rows, columns, **self.body)
        # Hancom never breaks a table laid out as a character (python-hwpx's default): rows past the page
        # are left off. Placed from the paragraph, not the column, it keeps the paragraph's indent.
        table.set_treat_as_char(False)
        table.element.find(f"{_HP}pos").set("horzRelTo", "PARA")
        _space_above_below(table)
        self.place(table.paragraph, quote, indent)
        return table

    # --- blocks -----------------------------------------------------------------------------

    def blocks(self, nodes: list[SyntaxTreeNode], quote: int = 0, indent: int = 0, level: int = 0,
               numbering: str | None = None) -> None:
        """Write *nodes*, inside *quote* quotes and *level* list items placed at *indent*.

        *numbering* is the numbering id of the numbered list item holding them, if any.
        """
        content = indent + min(level, LIST_MAX_LEVEL) * LIST_INDENT
        for k, node in enumerate(nodes):
            following = nodes[k + 1] if k + 1 < len(nodes) else None
            if node.type == "heading":
                runs = _runs(node.children[0].children)
                depth = int(node.tag[1])
                if depth not in self.heading_styles:
                    self.heading_styles[depth] = self.doc.styles.resolve(f"개요 {depth}")
                heading = self.doc.add_heading(_plain(runs), level=depth, style=self.heading_styles[depth])
                self.write_runs(heading, runs)
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

    def pieces(self, pieces: list[_Piece], following: str | None, quote: int, indent: int) -> None:
        """A paragraph's pieces become text paragraphs, pictures, captions, the TOC position and page breaks.

        *following* is the kind of the block after the paragraph (see _block_kind).
        """
        for k, piece in enumerate(pieces):
            if piece.kind == "toc":
                if self.toc_at is None:
                    self.toc_at = self.paragraph_count()
                    if self.toc_at in self.page_breaks:
                        self.page_breaks.remove(self.toc_at)
                        self.toc_page_break = True
            elif piece.kind == "page_break":
                self.page_breaks.append(self.paragraph_count())
            elif piece.kind == "image":
                self.picture(piece.image, quote, indent)
            else:
                # What a caption line would caption: the next piece of this paragraph, or the next block.
                next_kind = pieces[k + 1].kind if k + 1 < len(pieces) else following
                if not self.take_caption(piece.runs, next_kind):
                    self.text_paragraph(piece.runs, quote, indent)

    def picture(self, node: SyntaxTreeNode, quote: int, indent: int) -> None:
        image_path = self.base_dir / unquote(node.attrs["src"])
        data = image_path.read_bytes()
        if self.caption is None:  # without a "[그림] ..." line, the alt text (if any) is the caption
            alt = _strip(_runs(node.children))
            if alt:
                self.number_caption("그림", alt)
        max_height = self.body_height - 2 * OBJECT_SPACING - (CAPTION_RESERVE if self.caption is not None else 0)
        width, height = _picture_size(
            image_path,
            data,
            self.body_width,
            max_height,
            _length(node.attrs.get("width"), self.body_width),
            _length(node.attrs.get("height"), self.body_height),
        ) or (None, None)
        picture = self.doc.add_picture(
            data, image_path.suffix.lstrip(".").lower(), width=width, height=height, **self.body
        )
        _space_above_below(picture)
        self.place(picture.paragraph, quote, indent)
        self.set_caption(picture, "BOTTOM")

    def table(self, node: SyntaxTreeNode, quote: int, indent: int) -> None:
        rows = [tr for section in node.children for tr in section.children]
        cells = [[_runs(cell.children[0].children) if cell.children else [] for cell in tr.children] for tr in rows]
        aligns = [_ALIGN.get(cell.attrs.get("style", "")) for cell in rows[0].children]
        table = self.add_table(len(cells), len(cells[0]), quote, indent)
        # The header row's cells ("header") come again at the top of each page the table runs on to.
        table.element.set("repeatHeader", "1")
        table.set_column_widths(
            [max(MIN_COLUMN_WEIGHT, *(_display_width(_plain(row[c])) for row in cells)) for c in range(len(cells[0]))]
        )
        self.set_caption(table, "TOP")
        for r, row in enumerate(cells):
            for c, runs in enumerate(row):
                cell = table.cell(r, c)
                cell.text = _plain(runs)
                paragraph = cell.paragraphs[0]
                if aligns[c]:
                    paragraph.para_pr_id_ref = self.para_format(paragraph.para_pr_id_ref, alignment=aligns[c])
                if r == 0:
                    cell.element.set("header", "1")
                    paragraph.char_pr_id_ref = self.char_format(paragraph.char_pr_id_ref, bold=True)
                    table.set_cell_shading(r, c, HEADER_FILL)
                self.write_runs(paragraph, runs)

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
        for index in self.page_breaks:
            if index < len(paragraphs):
                paragraphs[index].element.set("pageBreak", "1")

        headings = [p for p, level, _ in outline_heading_paragraphs(self.doc) if level <= TOC_LEVEL]
        if self.toc_at is not None and headings:
            # Hancom's TOC update also collects 바탕글 paragraphs (e.g. table cells): move them to 본문 first.
            ensure_body_styles_not_collected(self.doc)
            # dirty: Hancom rebuilds the TOC, with real page numbers, when it opens the document.
            add_native_toc(
                self.doc, at_index=self.toc_at, title=TOC_TITLE, level=TOC_LEVEL, headings=headings, dirty=True
            )
            if self.toc_page_break:
                self.doc.paragraphs[self.toc_at].element.set("pageBreak", "1")


def _walk(node: SyntaxTreeNode) -> Iterable[SyntaxTreeNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def missing_images(markdown: str, base_dir: Path) -> list[tuple[int, Path]]:
    """(line number, path) of each image, outside code blocks, whose file is not in *base_dir*."""
    # Front matter becomes blank lines, so line numbers still count it.
    markdown = _FRONT_MATTER.sub(lambda m: "\n" * m.group(0).count("\n"), markdown, count=1)
    lines = markdown.splitlines()
    missing: list[tuple[int, Path]] = []
    for node in _walk(SyntaxTreeNode(PARSER.parse(markdown))):
        if node.type != "inline" or node.map is None:
            continue
        for image in (n for n in _walk(node) if n.type == "image"):
            src = image.attrs["src"]
            path = base_dir / unquote(src)
            if path.is_file():
                continue
            # The line of the paragraph that names the image.
            start, end = node.map
            raw = next((s for s in (src, unquote(src)) if any(s in line for line in lines[start:end])), None)
            number = next((i + 1 for i in range(start, end) if raw and raw in lines[i]), start + 1)
            missing.append((number, path))
    return missing


def read_markdown(md_path: Path) -> str:
    """Read *md_path* (UTF-8) without its front matter, which is metadata, not document content."""
    return _FRONT_MATTER.sub("", md_path.read_text(encoding="utf-8"), count=1)


def convert(md_path: Path, template_path: Path, out_path: Path) -> None:
    convert_markdown(read_markdown(md_path), md_path.parent, template_path, out_path)


def convert_markdown(markdown: str, base_dir: Path, template_path: Path, out_path: Path) -> None:
    """Convert *markdown*, whose image paths are relative to *base_dir*."""
    doc = HwpxDocument.open(template_path)
    root = SyntaxTreeNode(PARSER.parse(markdown))
    writer = _Writer(doc, base_dir, root)
    writer.blocks(root.children)
    writer.finish()

    report = doc.validate()
    if not report.ok:
        raise ConversionError(
            "변환한 문서가 HWPX 검증에 실패해 저장하지 않았습니다:\n" + "\n".join(str(issue) for issue in report.errors)
        )
    doc.save_to_path(out_path)
