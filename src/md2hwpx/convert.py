"""Convert a Markdown file to HWPX using a template document."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from hwpx import HwpxDocument
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

from md2hwpx.diagram import LANGS

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_IMAGE = re.compile(r"^\s*!\[[^\]]*\]\(([^)\s]+)\)\s*$")
# File names render_diagrams gives the PNGs it renders.
_DIAGRAM_IMAGE = re.compile(rf"^({'|'.join(LANGS)})-[0-9a-f]{{12}}\.png$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)")
# "[표] ..." / "[그림] ..." right before a table or code block / image (blank lines allowed) is its caption,
# written as "[표 N] ..." / "[그림 N] ..." numbered in document order.
_CAPTION = re.compile(r"^\s*\[(표|그림)\]\s*(.*?)\s*$")
# Inline code span: a backtick run closed by a run of the same length.
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
# List item: indent, marker ("-", "*", "+" or "1." / "1)"), at least one space, content.
_LIST_ITEM = re.compile(r"^( *)([-*+]|(\d{1,9})[.)])[ \t]+(\S.*)$")
_DELIMITER = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# (starts with ':', ends with ':') -> paragraph alignment; no colon keeps the default.
_ALIGN = {(True, False): "LEFT", (False, True): "RIGHT", (True, True): "CENTER"}
BODY_STYLE = "본문"
CAPTION_STYLE = "표그림제목"
HEADER_FILL = "#D9D9D9"
# A column is weighted by its longest cell (Hangul counts double), but never below this.
MIN_COLUMN_WEIGHT = 4
LIST_MAX_LEVEL = 3  # deeper items are kept at this level
LIST_INDENT = 1000  # HWPUNIT added to the left margin per list level, as the template's 개요 styles step
CODE_FONT = "D2Coding"
CODE_SIZE = 9  # pt
CODE_LINE_SPACING = 140  # %
CODE_HIGHLIGHT_STYLE = "default"  # Pygments style


def _body(doc: HwpxDocument) -> dict:
    # add_table / add_picture take only the style id from style=, so pass its paragraph shape too.
    return {"style": BODY_STYLE, "para_pr_id_ref": doc.styles.resolve(BODY_STYLE).para_pr_id_ref}


def _body_width(doc: HwpxDocument) -> int:
    """Width a 본문 paragraph can hold: the page text width minus the style's side margins."""
    size, page = doc.page.size(), doc.page.margins()
    margin = doc.styles.paragraph_property(doc.styles.resolve(BODY_STYLE).para_pr_id_ref).margin
    return size.width - page.left - page.right - page.gutter - int(margin.left) - int(margin.right)


def _write_inline(doc: HwpxDocument, paragraph, text: str) -> None:
    """Rewrite *paragraph* (holding *text* in one run) so code spans become D2Coding runs."""
    parts = _CODE_SPAN.split(text)  # [plain, ticks, code, plain, ticks, code, ..., plain]
    if len(parts) == 1:
        return
    base = paragraph.char_pr_id_ref
    code_pr = doc.styles.ensure_run(font=CODE_FONT, base_char_pr_id=base)
    paragraph.clear_text()
    for k in range(0, len(parts), 3):
        if parts[k]:
            paragraph.add_run(parts[k], char_pr_id_ref=base)
        if k + 2 < len(parts):
            code = parts[k + 2]
            # CommonMark: one space on both sides of the content is stripped.
            if len(code) > 2 and code[0] == code[-1] == " " and code.strip():
                code = code[1:-1]
            paragraph.add_run(code, char_pr_id_ref=code_pr)


def _set_caption(doc: HwpxDocument, obj, text: str, side: str) -> None:
    style = doc.styles.resolve(CAPTION_STYLE)
    caption = obj.set_caption(text, side=side, char_pr_id_ref=style.char_pr_id_ref)
    for paragraph in caption.paragraphs:
        paragraph.style_id_ref = style.id
        paragraph.para_pr_id_ref = style.para_pr_id_ref
        _write_inline(doc, paragraph, text)


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and _DELIMITER.match(lines[i + 1]) is not None
        and len(_cells(lines[i])) == len(_cells(lines[i + 1]))
    )


def _captioned(lines: list[str], i: int, kind: str) -> int | None:
    """Index of the table, code block or image line that the caption at *i* belongs to, or None."""
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if kind == "표" and (_is_table_start(lines, j) or (j < len(lines) and _FENCE.match(lines[j]))):
        return j
    if kind == "그림" and j < len(lines) and _IMAGE.match(lines[j]):
        return j
    return None


def _display_width(text: str) -> int:
    text = _CODE_SPAN.sub(lambda m: m.group(2), text)
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _add_table(doc: HwpxDocument, rows: list[list[str]], aligns: list[str | None], caption: str | None) -> None:
    table = doc.add_table(len(rows), len(rows[0]), **_body(doc))
    table.set_column_widths(
        [max(MIN_COLUMN_WEIGHT, *(_display_width(row[c]) for row in rows)) for c in range(len(rows[0]))]
    )
    if caption is not None:
        _set_caption(doc, table, caption, "TOP")
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.cell(r, c)
            cell.text = text
            paragraph = cell.paragraphs[0]
            if aligns[c]:
                paragraph.para_pr_id_ref = doc.styles.ensure_paragraph_format(
                    base_para_pr_id=paragraph.para_pr_id_ref, alignment=aligns[c]
                )
            if r == 0:
                paragraph.char_pr_id_ref = doc.styles.ensure_run(
                    bold=True, base_char_pr_id=paragraph.char_pr_id_ref
                )
                table.set_cell_shading(r, c, HEADER_FILL)
            _write_inline(doc, paragraph, text)


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


def _add_code_block(doc: HwpxDocument, code: list[str], lang: str, caption: str | None) -> None:
    table = doc.add_table(1, 1, **_body(doc))
    if caption is not None:
        _set_caption(doc, table, caption, "TOP")
    cell = table.cell(0, 0)
    cell.set_text("\n".join(code), split_paragraphs=True)
    char_prs: dict[tuple, str] = {}

    def char_pr(base: str | None, color: str | None, bold: bool, italic: bool) -> str:
        key = (base, color, bold, italic)
        if key not in char_prs:
            char_prs[key] = doc.styles.ensure_run(
                font=CODE_FONT, size=CODE_SIZE, color=color and f"#{color}",
                bold=bold, italic=italic, base_char_pr_id=base,
            )
        return char_prs[key]

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
        paragraph.para_pr_id_ref = doc.styles.ensure_paragraph_format(
            base_para_pr_id=paragraph.para_pr_id_ref, line_spacing_percent=CODE_LINE_SPACING
        )


def _ends_list(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(
        _HEADING.match(line) or _FENCE.match(line) or _IMAGE.match(line) or _CAPTION.match(line)
        or _is_table_start(lines, i)
    )


def _add_list(doc: HwpxDocument, lines: list[str], i: int) -> int:
    """Add the list starting at line *i* and return the index of the first line after it."""
    items: list[list] = []  # [level, number (None for a bullet), text]
    stack: list[int] = []  # content columns of the open items
    while i < len(lines):
        line = lines[i].expandtabs(4)
        if item := _LIST_ITEM.match(line):
            indent = len(item.group(1))
            while stack and indent < stack[-1]:
                stack.pop()
            stack.append(item.start(4))
            number = int(item.group(3)) if item.group(3) else None
            items.append([min(len(stack), LIST_MAX_LEVEL), number, item.group(4).strip()])
        elif not line.strip():
            # A blank line ends the list unless another item follows it.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _LIST_ITEM.match(lines[j].expandtabs(4)):
                i = j
                continue
            break
        elif _ends_list(lines, i):
            break
        else:
            items[-1][2] += " " + line.strip()  # continuation line of the item
        i += 1

    body_left = int(doc.styles.paragraph_property(doc.styles.resolve(BODY_STYLE).para_pr_id_ref).margin.left)
    for n, (level, number, text) in enumerate(items):
        paragraph = doc.add_paragraph(text, style=BODY_STYLE)
        _write_inline(doc, paragraph, text)
        index = len(doc.paragraphs) - 1
        if number is None:
            doc.styles.apply_list_format(paragraph_index=index, kind="bullet", level=level)
        else:
            continued = _continues_numbering(items, n)
            doc.styles.apply_list_format(
                paragraph_index=index, kind="number", level=level, continue_list=continued,
                start=None if continued or number == 1 else number,
            )
        paragraph.para_pr_id_ref = doc.styles.ensure_paragraph_format(
            base_para_pr_id=paragraph.para_pr_id_ref, margins={"left": body_left + (level - 1) * LIST_INDENT}
        )
    return i


def _continues_numbering(items: list[list], n: int) -> bool:
    """Whether numbered item *n* goes on with the numbering before it.

    It does after a numbered item at the same level of the same list, or as the
    first child of a numbered item (Hancom numbers sub-levels within one definition).
    """
    level = items[n][0]
    for prev_level, prev_number, _ in reversed(items[:n]):
        if prev_level <= level:
            return prev_number is not None
    return False


def convert(md_path: Path, template_path: Path, out_path: Path) -> None:
    convert_markdown(md_path.read_text(encoding="utf-8"), md_path.parent, template_path, out_path)


def convert_markdown(markdown: str, base_dir: Path, template_path: Path, out_path: Path) -> None:
    """Convert *markdown*, whose image paths are relative to *base_dir*."""
    doc = HwpxDocument.open(template_path)
    lines = markdown.splitlines()
    paragraph: list[str] = []
    caption: str | None = None
    numbers = {"표": 0, "그림": 0}

    def flush() -> None:
        if paragraph:
            text = " ".join(paragraph)
            _write_inline(doc, doc.add_paragraph(text, style=BODY_STYLE), text)
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if fence := _FENCE.match(line):
            flush()
            close = re.compile(rf"^ {{0,3}}{re.escape(fence.group(1)[0])}{{{len(fence.group(1))},}}\s*$")
            i += 1
            code = []
            while i < len(lines) and not close.match(lines[i]):
                code.append(lines[i])
                i += 1
            _add_code_block(doc, code, fence.group(2), caption)
            caption = None
        elif _is_table_start(lines, i):
            flush()
            header = _cells(line)
            aligns = [_ALIGN.get((d.startswith(":"), d.endswith(":"))) for d in _cells(lines[i + 1])]
            rows = [header]
            i += 2
            while i < len(lines) and lines[i].strip() and not _HEADING.match(lines[i]) and not _FENCE.match(lines[i]):
                rows.append((_cells(lines[i]) + [""] * len(header))[: len(header)])
                i += 1
            _add_table(doc, rows, aligns, caption)
            caption = None
            continue
        elif heading := _HEADING.match(line):
            flush()
            _write_inline(doc, doc.add_heading(heading.group(2), level=len(heading.group(1))), heading.group(2))
        elif image := _IMAGE.match(line):
            flush()
            image_path = base_dir / image.group(1)
            width = _body_width(doc) if _DIAGRAM_IMAGE.match(image_path.name) else None
            picture = doc.add_picture(
                image_path.read_bytes(), image_path.suffix.lstrip(".").lower(), width=width, **_body(doc)
            )
            if caption is not None:
                _set_caption(doc, picture, caption, "BOTTOM")
                caption = None
        elif (match := _CAPTION.match(line)) and (target := _captioned(lines, i, match.group(1))) is not None:
            flush()
            kind = match.group(1)
            numbers[kind] += 1
            caption = f"[{kind} {numbers[kind]}] {match.group(2)}"
            i = target
            continue
        elif (item := _LIST_ITEM.match(line.expandtabs(4))) and not (
            # As in CommonMark, only a list starting at 1 can interrupt a paragraph.
            paragraph and item.group(3) and int(item.group(3)) != 1
        ):
            flush()
            i = _add_list(doc, lines, i)
            continue
        elif line.strip():
            paragraph.append(line.strip())
        else:
            flush()
        i += 1
    flush()

    doc.save_to_path(out_path)
