"""Table layout: column widths that keep cell text on one line where they can, and row heights that fit it."""

from __future__ import annotations

from dataclasses import replace
from typing import NamedTuple

from hwpx.form_fit.measure import (
    TextStyle, estimate_text_width, hancom_line_starts, indented_widths, text_style_from_refs,
)
from hwpx.oxml.table_sizes import cell_margins_of, grid_widths_of

_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
# Table cell padding, as Hancom's own new tables have it: 1.8 mm left and right, 0.5 mm above and below.
CELL_MARGIN_LR = 510  # HWPUNIT (1.8 mm)
CELL_MARGIN_TB = 141  # HWPUNIT (0.5 mm)
# Faces python-hwpx has no widths for, measured as a listed face of the same widths. D2Coding (code) is fixed
# pitch, half an em to a Latin character and an em to Hangul, as 굴림체 is; python-hwpx would guess it
# proportional, and a line of code came out narrower or wider than Hancom draws it.
STAND_IN_FACES = {"D2Coding": "굴림체"}
_FACES = ("glyph_face", "hangul_face", "latin_face", "symbol_face")


def _text_width(text: str, size: int) -> int:
    """*text* on one line at *size* pt, in HWPUNIT."""
    return round(estimate_text_width(text, size))


def column_widths(cells: list[list[str]], total: int, size: int) -> list[int]:
    """The width of each column of the table whose cell texts are *cells* (a cell's lines apart by "\n"), in
    HWPUNIT, adding up to *total*.

    A column is first as wide as its longest cell's text at *size* pt plus the cell padding, so that text
    stays on one line; with room to spare the extra goes to the columns that asked for more. Only when the
    text does not fit does a column come down, and then never below its longest word, which could not wrap
    anyway.
    """
    columns = len(cells[0])
    one_line: list[int] = []  # the widest line of the column, and the width it wants
    longest_word: list[int] = []  # its longest unbreakable word: the narrowest the column can be
    for c in range(columns):
        texts = [row[c] for row in cells]
        lines = [line for text in texts for line in text.split("\n")]
        one_line.append(max(_text_width(line, size) for line in lines) + CELL_MARGIN_LR * 2)
        words = [word for text in texts for word in text.split()]
        longest_word.append(max((_text_width(word, size) for word in words), default=0) + CELL_MARGIN_LR * 2)

    if sum(one_line) <= total:
        return _share(total, one_line)
    if sum(longest_word) <= total:
        # Room for every word, not for every line: what the wrap costs comes off the columns in proportion
        # to it, so the narrow ones keep their words whole.
        wrapped = [one - word for one, word in zip(one_line, longest_word)]
        return [word + part for word, part in zip(longest_word, _share(total - sum(longest_word), wrapped))]
    # Even the words do not fit: share the width out by how much each column wants.
    return _share(total, longest_word)


def percent_widths(percents: list[float], total: int) -> list[int]:
    """The width of each column at *percents* of *total*, adding up to *total*: percents that do not add
    up to 100 are scaled to."""
    return _share(total, percents)


def _share(total: int, weights: list[float]) -> list[int]:
    """*total* split in proportion to *weights*, the parts adding up to exactly *total*."""
    if not any(weights):
        weights = [1] * len(weights)
    total_weight = sum(weights)
    parts = [round(total * weight / total_weight) for weight in weights[:-1]]
    return [*parts, total - sum(parts)]


class TextStyles:
    """The Hancom layout settings of paragraph and character shapes (text_style_from_refs), and the sizes of
    character shapes, for one document: each worked out once.

    python-hwpx reads a shape back out of header.xml each time it is asked, which took most of a long
    document's conversion time, the same few shapes asked for again for every cell. A shape does not change
    once it is made: a changed look is a new shape, with its own id.
    """

    def __init__(self, doc) -> None:
        self.doc = doc
        self._styles: dict[tuple, TextStyle] = {}
        self._sizes: dict[str, float | None] = {}

    def style(self, para_pr_id_ref, char_pr_id_refs: list) -> TextStyle:
        """text_style_from_refs(doc, *para_pr_id_ref*, *char_pr_id_refs*)."""
        key = (str(para_pr_id_ref), tuple(str(ref) for ref in char_pr_id_refs))
        if key not in self._styles:
            self._styles[key] = text_style_from_refs(self.doc, para_pr_id_ref, char_pr_id_refs)
        return self._styles[key]

    def font_pt(self, char_pr_id_ref) -> float | None:
        """The size of the character shape *char_pr_id_ref* in pt, or None if it has none."""
        key = str(char_pr_id_ref)
        if key not in self._sizes:
            height = self.doc.styles.char_property(char_pr_id_ref).attributes.get("height")
            self._sizes[key] = int(height) / 100 if height else None
        return self._sizes[key]


class _Slot(NamedTuple):
    """Where a cell lays its text out: its line width, the size of its first run and its first paragraph's
    layout settings."""

    line_width: float
    font_pt: float
    text_style: TextStyle


def _slot(cell, grid: dict, text_styles: TextStyles) -> _Slot:
    """The slot of *cell*, whose table's column grid widths (grid_widths_of) are *grid*: what python-hwpx's
    resolve_slot_metrics(cell, doc, safety=1.0) gives of it, without the height budget it works out as well,
    which the rows do not need."""
    margins = cell_margins_of(cell.element, cell.table.element)
    left, right = (margins.left, margins.right) if margins is not None else (0, 0)
    width = float(grid.get(cell.element, cell.width))
    paragraphs = cell.paragraphs
    # The first run's size that is set, as python-hwpx takes it; 10pt without one.
    sizes = (text_styles.font_pt(run.char_pr_id_ref) for paragraph in paragraphs for run in paragraph.runs)
    font_pt = next((size for size in sizes if size is not None), 10.0)
    first = paragraphs[0]
    style = text_styles.style(first.para_pr_id_ref, [run.char_pr_id_ref for run in first.runs])
    return _Slot(max(width - left - right, 0.0), font_pt, style)


def _listed_faces(style: TextStyle) -> TextStyle:
    """*style* with its faces python-hwpx has no widths for swapped for their STAND_IN_FACES."""
    changes = {name: STAND_IN_FACES[getattr(style, name)] for name in _FACES if getattr(style, name) in STAND_IN_FACES}
    return replace(style, **changes) if changes else style


def line_count(
    text_styles: TextStyles, para_pr_id_ref, text: str, runs: list[tuple[str, object]], line_width: float,
    font_pt: float, style: TextStyle,
) -> int:
    """The lines *text*, its *runs* as (text, character shape id), takes in a paragraph of shape *para_pr_id_ref*
    *line_width* wide: each run measured in its own face, so inline code amid the text takes the code's width;
    the line breaking rules and indent are *style*'s, the paragraph's own, at *font_pt*."""
    if not text:
        return 1
    styles: list[TextStyle] = []
    for run_text, char_pr_id_ref in runs:
        styles += [_listed_faces(text_styles.style(para_pr_id_ref, [char_pr_id_ref]))] * len(run_text)
    style = _listed_faces(style)
    first, rest = indented_widths(line_width - style.margin_left - style.margin_right, style)
    return len(hancom_line_starts(text, [first, rest], font_pt, style, styles=styles))


def _paragraph_lines(paragraph, text_styles: TextStyles, slot: _Slot) -> int:
    """The lines *paragraph* takes in the cell whose *slot* it is (see line_count)."""
    runs = [(run.text, run.char_pr_id_ref) for run in paragraph.runs]
    return line_count(
        text_styles, paragraph.para_pr_id_ref, paragraph.text, runs, slot.line_width, slot.font_pt, slot.text_style
    )


def _cell_height(cell, grid: dict, text_styles: TextStyles, line_height: int) -> int:
    """The height *cell* needs for its text at *line_height* a line, in HWPUNIT: at least one line.

    Count the lines with Hancom's own breaking rules (python-hwpx's hancom_line_starts), not by characters:
    a run of '-' is half as wide again as a letter, and a cell of a narrow column wraps where a wide one
    does not.

    The whole width (safety=1.0), not python-hwpx's default 93% of it: measured against 93%, text taking
    93-100% of a line took a line more than Hancom laid it out in, and the row was a line taller. Measured
    at the whole width, each run in its own face (D2Coding as STAND_IN_FACES), the count matched Hancom's
    on every cell tried: plain, bold, header, partly bold, inline code, links and code blocks.
    """
    slot = _slot(cell, grid, text_styles)
    lines = sum(_paragraph_lines(paragraph, text_styles, slot) for paragraph in cell.paragraphs)
    return max(lines, 1) * line_height + CELL_MARGIN_TB * 2


def fit_rows(table, text_styles: TextStyles, line_height: int, header_line_height: int | None = None) -> None:
    """Declare each row of *table* as tall as its tallest cell's text, and the table as their sum: the lines
    *line_height* each, the first row's *header_line_height* if given.

    A cell declares its own height, and Hancom lays the text out inside it: when the declaration is
    shorter than the text, the table takes more room on the page than it says it will, which pushes the
    block after it to the next page and leaves the space it should have filled empty.
    """
    grid = grid_widths_of(table.element)  # the cells' widths, which the rows' heights do not change
    total = 0
    for r in range(table.row_count):
        cells = [table.cell(r, c) for c in range(table.column_count)]
        row_line_height = header_line_height if r == 0 and header_line_height else line_height
        height = max(_cell_height(cell, grid, text_styles, row_line_height) for cell in cells)
        for cell in cells:
            cell.set_size(height=height)
        total += height
    table.element.find(f"{_HP}sz").set("height", str(total))
