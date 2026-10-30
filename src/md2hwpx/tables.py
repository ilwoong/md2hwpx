"""Table layout: column widths that keep cell text on one line where they can, and row heights that fit it."""

from __future__ import annotations

from hwpx.form_fit.measure import estimate_text_width, measure, resolve_slot_metrics

_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
# Table cell padding, as Hancom's own new tables have it: 1.8 mm left and right, 0.5 mm above and below.
CELL_MARGIN_LR = 510  # HWPUNIT (1.8 mm)
CELL_MARGIN_TB = 141  # HWPUNIT (0.5 mm)


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


def _share(total: int, weights: list[int]) -> list[int]:
    """*total* split in proportion to *weights*, the parts adding up to exactly *total*."""
    if not any(weights):
        weights = [1] * len(weights)
    total_weight = sum(weights)
    parts = [round(total * weight / total_weight) for weight in weights[:-1]]
    return [*parts, total - sum(parts)]


def _cell_height(cell, doc, line_height: int) -> int:
    """The height *cell* needs for its text at *line_height* a line, in HWPUNIT: at least one line.

    Count the lines with Hancom's own breaking rules (resolve_slot_metrics + measure), not by characters:
    a run of '-' is half as wide again as a letter, and a cell of a narrow column wraps where a wide one
    does not.
    """
    slot = resolve_slot_metrics(cell, doc)
    lines = sum(measure(paragraph.text, slot).lines for paragraph in cell.paragraphs)
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
