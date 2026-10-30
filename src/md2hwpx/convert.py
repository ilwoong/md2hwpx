"""Convert a Markdown file to HWPX using a template document."""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

from hwpx import HwpxDocument
from hwpx.form_fit.measure import estimate_text_width, measure, resolve_slot_metrics
from hwpx.oxml import Style
from hwpx.oxml.hyperlink_form import hyperlink_char_pr
from hwpx.tools.toc_author import add_native_toc, ensure_body_styles_not_collected, outline_heading_paragraphs
from markdown_it.tree import SyntaxTreeNode
from PIL import Image
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
_MARKERS = {
    # "[TOC]", or "[TOC 그림 표 제목]": the lists to make, in that order. "링크없음" leaves out the links.
    "toc": re.compile(r"\[TOC((?:[ \t]+(?:제목|표|그림|수식|링크없음))*)[ \t]*\]", re.IGNORECASE),
    # "\newpage", or "\newpage 쪽 표 그림 수식": those numbers start over on the new page.
    # "쪽숨김" hides the new page's page number, header and footer.
    "page_break": re.compile(r"\\(?:newpage|pagebreak)((?:[ \t]+(?:쪽숨김|쪽|표|그림|수식))*)[ \t]*"),
    # "[여백면]" or "[여백면] 문구": a page kept as margin, with that text centred on it.
    "blank_page": re.compile(r"\[\s*여백면\s*\]\s*(.*)"),
}
_MARKS = {"strong": "bold", "em": "italic", "s": "strike"}
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HP_P = f"{_HP}p"
# An image's width / height attribute: percent of the page body, or millimetres.
_LENGTH = re.compile(r"(\d+(?:\.\d+)?)(%|mm)")
# Table cell style "text-align:..." -> paragraph alignment; no style keeps the default.
_ALIGN = {"text-align:left": "LEFT", "text-align:center": "CENTER", "text-align:right": "RIGHT"}
# Shown in place of an image whose file is not there, or that cannot be read.
MISSING_IMAGE = Path(__file__).resolve().parent / "static" / "missing-image.png"
SVG_TIMEOUT = 60  # seconds for rsvg-convert
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
# Hancom's running numbers (hp:newNum / hp:autoNum numType) a "\newpage" line can start over.
NUMBERS = {"쪽": "PAGE", "표": "TABLE", "그림": "PICTURE", "수식": "EQUATION"}
HIDE_PAGE = "쪽숨김"  # on a "\newpage" line: that page shows no page number, header or footer
HWPUNIT_PER_PIXEL = 75  # 7200 HWPUNIT per inch at 96 DPI
HWPUNIT_PER_MM = 7200 / 25.4  # 7200 HWPUNIT per inch
CAPTION_RESERVE = 2500  # HWPUNIT kept below a picture for its caption: gap 850 + one 10pt line at 160%
# A picture is laid out as a character in its paragraph, so it follows that paragraph's alignment: one
# narrower than the body is centred rather than left at the margin.
PICTURE_REDUCED_ALIGN = "CENTER"
# Table cell padding, as Hancom's own new tables have it: 1.8 mm left and right, 0.5 mm above and below.
CELL_MARGIN_LR = 510  # HWPUNIT (1.8 mm)
CELL_MARGIN_TB = 141  # HWPUNIT (0.5 mm)
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
    """Part of a paragraph: text "runs", an "image", or a "toc" / "page_break" / "blank_page" marker line."""

    kind: str
    runs: list[Run] | None = None
    image: SyntaxTreeNode | None = None
    text: str = ""  # a blank page's wording, or the TOC or page break marker's words (see _MARKERS)


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


class ImageError(Exception):
    """An image that cannot go in the HWPX. The message is a Korean clause ending in "-어" / "-해",
    which a warning goes on from ("... 대체 이미지를 넣었습니다")."""


def _image_format(data: bytes) -> str | None:
    """The format of a PNG, JPEG or GIF image, as the HWPX names it, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def picture_data(path: Path) -> tuple[bytes, str]:
    """The image at *path* as (data, format) to put in the HWPX: PNG, JPEG and GIF as they are, any other
    image as PNG (SVG by rsvg-convert, the rest by Pillow). Raises ImageError if it cannot be either."""
    if not path.is_file():
        raise ImageError("이미지 파일이 없어")
    data = path.read_bytes()
    image_format = _image_format(data)
    if image_format is not None:
        return data, image_format
    if path.suffix.lower() == ".svg" or b"<svg" in data[:4096]:
        return _svg_to_png(data), "png"
    try:
        with Image.open(io.BytesIO(data)) as image:
            png = io.BytesIO()
            image.convert("RGBA").save(png, "PNG")  # RGBA: PNG cannot hold every mode (CMYK, ...)
    # UnidentifiedImageError is an OSError; DecompressionBombError, for one too large to open, is not.
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageError("이미지 형식을 읽을 수 없어") from None
    return png.getvalue(), "png"


def _svg_to_png(svg: bytes) -> bytes:
    """*svg* drawn as PNG at 96 DPI by rsvg-convert. Given on stdin, it has no base to load files from."""
    try:
        result = subprocess.run(["rsvg-convert", "--format=png"], input=svg, capture_output=True, timeout=SVG_TIMEOUT)
    except FileNotFoundError:
        raise ImageError("SVG 를 변환할 rsvg-convert 가 없어") from None
    except subprocess.TimeoutExpired:
        raise ImageError("SVG 변환이 너무 오래 걸려") from None
    if result.returncode != 0 or _image_format(result.stdout) != "png":
        raise ImageError("SVG 를 변환하지 못해")
    return result.stdout


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


def _text_width(text: str, size: int) -> int:
    """*text* on one line at *size* pt, in HWPUNIT."""
    return round(estimate_text_width(text, size))


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


def _column_widths(cells: list[list[list[Run]]], total: int, size: int) -> list[int]:
    """The width of each column, in HWPUNIT, adding up to *total*.

    A column is first as wide as its longest cell's text at *size* pt plus the cell padding, so that text
    stays on one line; with room to spare the extra goes to the columns that asked for more. Only when the
    text does not fit does a column come down, and then never below its longest word, which could not wrap
    anyway.
    """
    columns = len(cells[0])
    one_line: list[int] = []  # the widest cell of the column, and the width it wants
    longest_word: list[int] = []  # its longest unbreakable word: the narrowest the column can be
    for c in range(columns):
        texts = [_plain(row[c]) for row in cells]
        one_line.append(max(_text_width(text, size) for text in texts) + CELL_MARGIN_LR * 2)
        words = [word for text in texts for word in text.split()]
        longest_word.append(max((_text_width(word, size) for word in words), default=0) + CELL_MARGIN_LR * 2)

    if sum(one_line) <= total:
        return _share(total, one_line)
    if sum(longest_word) <= total:
        # Room for every word, not for every line: what the wrap costs comes off the columns in proportion
        # to it, so the narrow ones keep their words whole.
        wrapped = [one - word for one, word in zip(one_line, longest_word)]
        return [word + share for word, share in zip(longest_word, _share(total - sum(longest_word), wrapped))]
    # Even the words do not fit: share the width out by how much each column wants.
    return _share(total, longest_word)


def _share(total: int, weights: list[int]) -> list[int]:
    """*total* split in proportion to *weights*, the parts adding up to exactly *total*."""
    if not any(weights):
        weights = [1] * len(weights)
    total_weight = sum(weights)
    parts = [round(total * weight / total_weight) for weight in weights[:-1]]
    return [*parts, total - sum(parts)]


def _space_above_below(obj) -> None:
    """Keep about a line of space above and below the table or picture *obj*, none at its sides."""
    margin = obj.element.find(f"{_HP}outMargin")
    margin.set("top", str(OBJECT_SPACING))
    margin.set("bottom", str(OBJECT_SPACING))


def _cell_height(cell, doc, line_height: int) -> int:
    """The height *cell* needs for its text at *line_height* a line, in HWPUNIT: at least one line.

    Count the lines with Hancom's own breaking rules (resolve_slot_metrics + measure), not by characters:
    a run of '-' is half as wide again as a letter, and a cell of a narrow column wraps where a wide one
    does not.
    """
    slot = resolve_slot_metrics(cell, doc)
    lines = sum(measure(paragraph.text, slot).lines for paragraph in cell.paragraphs)
    return max(lines, 1) * line_height + CELL_MARGIN_TB * 2


def _fit_rows(table, doc, line_height: int) -> None:
    """Declare each row of *table* as tall as its tallest cell's text, and the table as their sum.

    A cell declares its own height, and Hancom lays the text out inside it: when the declaration is
    shorter than the text, the table takes more room on the page than it says it will, which pushes the
    block after it to the next page and leaves the space it should have filled empty.
    """
    total = 0
    for r in range(table.row_count):
        cells = [table.cell(r, c) for c in range(table.column_count)]
        height = max(_cell_height(cell, doc, line_height) for cell in cells)
        for cell in cells:
            cell.set_size(height=height)
        total += height
    table.element.find(f"{_HP}sz").set("height", str(total))


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
            # The blank page marker carries a wording, the TOC and page break markers their words, in their one group.
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


class _Writer:
    """Writes the block tree of a Markdown document into an HWPX document."""

    def __init__(self, doc: HwpxDocument, base_dir: Path, root: SyntaxTreeNode):
        self.doc = doc
        self.base_dir = base_dir
        # Styles are resolved once: python-hwpx looks a style name up by walking all of header.xml,
        # which grows with every character and paragraph shape the conversion adds.
        self.body_style = doc.styles.resolve(BODY_STYLE)
        self.plain_style = doc.styles.resolve(PLAIN_STYLE)
        self.caption_style = doc.styles.resolve(CAPTION_STYLE)
        self.heading_styles: dict[int, Style] = {}
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
        self.caption: list[Run] | None = None  # for the next table, code block or picture
        # Caption numbers count within the chapter: "표 2-1" is the first table of the second level 1 heading.
        self.chapter = 0  # the level 1 heading being written, 0 before the first one
        self.numbers = {"표": 0, "그림": 0}  # within that chapter, or since a "\newpage 표" / "\newpage 그림"
        self.caption_number: tuple[str, int, str] | None = None  # the caption's (text before it, number, numType)
        self.toc_at: int | None = None  # paragraph index of the first [TOC] marker
        self.toc_page_break = False  # a page break right before the [TOC] marker
        self.toc_kinds: list[str] = []  # the lists the [TOC] marker names, in order (none: all in one field)
        self.toc_links = True  # entries are hyperlinks, unless the [TOC] marker says 링크없음
        self.page_breaks: list[int] = []  # indexes of the paragraphs that start a new page
        self.restarts: dict[int, set[str]] = {}  # paragraph index -> numTypes that start over at 1 there
        self.toc_restarts: set[str] = set()  # numTypes that start over at the TOC
        self.hidden_pages: set[int] = set()  # indexes of the paragraphs whose page hides its number
        self.toc_hidden = False  # the TOC's first page hides its number
        # Paragraphs already there when the conversion starts (see new_page).
        self._first_content = self.paragraph_count()
        self._h2_seen = 0  # level 2 headings in the chapter being written (see heading)

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

    def new_page(self) -> None:
        """Start a new page at the next paragraph, unless nothing is written yet: the first page is new.

        The [TOC] marker counts as written, though its paragraphs go in only in finish().
        """
        if self.paragraph_count() > self._first_content or self.toc_at is not None:
            self.page_breaks.append(self.paragraph_count())

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
            # Hancom numbers every table and picture, captioned or not: keep this one out of the count.
            obj.element.set("numberingType", "NONE")
            return
        style = self.caption_style
        caption = obj.set_caption(_plain(self.caption), side=side, char_pr_id_ref=style.char_pr_id_ref)
        for paragraph in caption.paragraphs:
            paragraph.style_id_ref = style.id
            paragraph.para_pr_id_ref = style.para_pr_id_ref
            self.write_runs(paragraph, self.caption, footnotes=False)
        _auto_number(caption.paragraphs[0], *self.caption_number)
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

        The count becomes Hancom's own number (see _auto_number), which starts over where the count does.
        """
        self.numbers[kind] += 1
        before = f"[{kind} {self.chapter}-" if self.chapter else f"[{kind} "
        self.caption_number = (before, self.numbers[kind], NUMBERS[kind])
        self.caption = [Run(f"{before}{self.numbers[kind]}] "), *title]

    def restart(self, at: int, names: Iterable[str]) -> None:
        """Start the numbers *names* (keys of NUMBERS) over at 1 from the paragraph at index *at*."""
        for name in names:
            self.restarts.setdefault(at, set()).add(NUMBERS[name])
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
        # The height each row needs for its text is set by the caller, once the text is in (see _fit_rows).
        padding = table.element.find(f"{_HP}inMargin")
        for side, value in (("left", CELL_MARGIN_LR), ("right", CELL_MARGIN_LR),
                            ("top", CELL_MARGIN_TB), ("bottom", CELL_MARGIN_TB)):
            padding.set(side, str(value))
        self.place(table.paragraph, quote, indent)
        return table

    # --- blocks -----------------------------------------------------------------------------

    def heading(self, node: SyntaxTreeNode) -> None:
        """Write a heading, with the page break and caption number reset its level asks for."""
        runs = _runs(node.children[0].children)
        depth = int(node.tag[1])
        if depth not in self.heading_styles:
            self.heading_styles[depth] = self.doc.styles.resolve(f"개요 {depth}")
        # Anything before it (see new_page), counted ahead of the empty paragraph below.
        written = self.paragraph_count() > self._first_content or self.toc_at is not None
        if depth > 1:
            # An empty 본문 paragraph before a sub-heading: the template's 개요 styles carry no
            # space above, so the gap from what came before is that paragraph's own line.
            self.doc.add_paragraph(style=self.body_style)
        if depth == 1:
            self._h2_seen = 0  # the next level 2 heading is a chapter's first
            # A new chapter starts the caption numbers over: 표 2-1 follows 표 1-4.
            self.chapter += 1
            self.restart(self.paragraph_count(), self.numbers)
            self.new_page()  # a chapter opens a page
        elif depth == 2:
            self._h2_seen += 1
            # Within a chapter the first level 2 heading stays on the chapter's page; the ones after it,
            # and those before any chapter, start a page of their own (unless the document starts with it).
            if self._h2_seen > 1 or (not self.chapter and written):
                self.page_breaks.append(self.paragraph_count())
        heading = self.doc.add_heading(_plain(runs), level=depth, style=self.heading_styles[depth])
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
                    if self.toc_at in self.page_breaks:
                        self.page_breaks.remove(self.toc_at)
                        self.toc_page_break = True
                    self.toc_restarts = self.restarts.pop(self.toc_at, set())
                    self.toc_hidden = self.toc_at in self.hidden_pages
                    self.hidden_pages.discard(self.toc_at)
            elif piece.kind == "page_break":
                # At the very start it only carries its restarts and hiding: the first page is new already.
                self.new_page()
                words = piece.text.split()
                self.restart(self.paragraph_count(), (w for w in words if w != HIDE_PAGE))
                if HIDE_PAGE in words:
                    self.hidden_pages.add(self.paragraph_count())
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
        width, height = _picture_size(
            image_path,
            data,
            self.body_width,
            max_height,
            _length(node.attrs.get("width"), self.body_width),
            _length(node.attrs.get("height"), self.body_height),
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
        self.page_breaks.append(self.paragraph_count())

    def table(self, node: SyntaxTreeNode, quote: int, indent: int) -> None:
        rows = [tr for section in node.children for tr in section.children]
        cells = [[_runs(cell.children[0].children) if cell.children else [] for cell in tr.children] for tr in rows]
        aligns = [_ALIGN.get(cell.attrs.get("style", "")) for cell in rows[0].children]
        table = self.add_table(len(cells), len(cells[0]), quote, indent)
        # The header row's cells ("header") come again at the top of each page the table runs on to.
        table.element.set("repeatHeader", "1")
        table.set_column_widths(_column_widths(cells, self.body_width, self.plain_size))
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
        _fit_rows(table, self.doc, self.plain_line_height)

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
        _fit_rows(table, self.doc, CODE_LINE_HEIGHT)

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
        for index, num_types in self.restarts.items():
            if index < len(paragraphs):
                _new_numbers(paragraphs[index], num_types)
        for index in self.hidden_pages:
            if index < len(paragraphs):
                _hide_page(paragraphs[index])

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
            if self.toc_page_break:
                self.doc.paragraphs[self.toc_at].element.set("pageBreak", "1")
            _new_numbers(self.doc.paragraphs[self.toc_at], self.toc_restarts)
            if self.toc_hidden:
                _hide_page(self.doc.paragraphs[self.toc_at])


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


def image_paths(markdown: str, base_dir: Path) -> list[tuple[int, Path]]:
    """(line number, path) of each image outside code blocks, its path taken from *base_dir*."""
    # Front matter becomes blank lines, so line numbers still count it.
    markdown = _FRONT_MATTER.sub(lambda m: "\n" * m.group(0).count("\n"), markdown, count=1)
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
