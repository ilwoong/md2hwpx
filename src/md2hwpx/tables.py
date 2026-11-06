"""Table layout: column widths that keep cell text on one line where they can, and row heights that fit it."""

from __future__ import annotations

from dataclasses import replace

from hwpx.form_fit.measure import (
    TextStyle, estimate_text_width, hancom_line_starts, indented_widths, resolve_slot_metrics, text_style_from_refs,
)

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
    """The width of each column of the table whose cell texts are *cells*, in HWPUNIT, adding up to *total*.

    A column is first as wide as its longest cell's text at *size* pt plus the cell padding, so that text
    stays on one line; with room to spare the extra goes to the columns that asked for more. Only when the
    text does not fit does a column come down, and then never below its longest word, which could not wrap
    anyway.
    """
    columns = len(cells[0])
    one_line: list[int] = []  # the widest cell of the column, and the width it wants
    longest_word: list[int] = []  # its longest unbreakable word: the narrowest the column can be
    for c in range(columns):
        texts = [row[c] for row in cells]
        one_line.append(max(_text_width(text, size) for text in texts) + CELL_MARGIN_LR * 2)
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


def _listed_faces(style: TextStyle) -> TextStyle:
    """*style* with its faces python-hwpx has no widths for swapped for their STAND_IN_FACES."""
    changes = {name: STAND_IN_FACES[getattr(style, name)] for name in _FACES if getattr(style, name) in STAND_IN_FACES}
    return replace(style, **changes) if changes else style


def _paragraph_lines(paragraph, doc, slot) -> int:
    """The lines *paragraph* takes in the cell whose *slot* it is: each run measured in its own face, so inline
    code amid the text takes the code's width; the line breaking rules and indent are the cell's."""
    text = paragraph.text
    if not text:
        return 1
    styles: list[TextStyle] = []
    for run in paragraph.runs:
        style = _listed_faces(text_style_from_refs(doc, paragraph.para_pr_id_ref, [run.char_pr_id_ref]))
        styles += [style] * len(run.text)
    style = _listed_faces(slot.text_style)
    first, rest = indented_widths(slot.line_width - style.margin_left - style.margin_right, style)
    return len(hancom_line_starts(text, [first, rest], slot.font_pt, style, styles=styles))


def _cell_height(cell, doc, line_height: int) -> int:
    """The height *cell* needs for its text at *line_height* a line, in HWPUNIT: at least one line.

    Count the lines with Hancom's own breaking rules (python-hwpx's hancom_line_starts), not by characters:
    a run of '-' is half as wide again as a letter, and a cell of a narrow column wraps where a wide one
    does not.

    The whole width (safety=1.0), not python-hwpx's default 93% of it: measured against 93%, text taking
    93-100% of a line took a line more than Hancom laid it out in, and the row was a line taller. Measured
    at the whole width, each run in its own face (D2Coding as STAND_IN_FACES), the count matched Hancom's
    on every cell tried: plain, bold, header, partly bold, inline code, links and code blocks.
    """
    slot = resolve_slot_metrics(cell, doc, safety=1.0)
    lines = sum(_paragraph_lines(paragraph, doc, slot) for paragraph in cell.paragraphs)
    return max(lines, 1) * line_height + CELL_MARGIN_TB * 2


def fit_rows(table, doc, line_height: int) -> None:
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
