"""Convert a Markdown file to HWPX using a template document."""

from __future__ import annotations

import re
from pathlib import Path

from hwpx import HwpxDocument
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_IMAGE = re.compile(r"^\s*!\[[^\]]*\]\(([^)\s]+)\)\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)")
# "[표] ..." / "[그림] ..." right before a table or code block / image (blank lines allowed) is its caption,
# written as "[표 N] ..." / "[그림 N] ..." numbered in document order.
_CAPTION = re.compile(r"^\s*\[(표|그림)\]\s*(.*?)\s*$")
_DELIMITER = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# (starts with ':', ends with ':') -> paragraph alignment; no colon keeps the default.
_ALIGN = {(True, False): "LEFT", (False, True): "RIGHT", (True, True): "CENTER"}
BODY_STYLE = "본문"
CAPTION_STYLE = "표그림제목"
HEADER_FILL = "#D9D9D9"
CODE_FONT = "D2Coding"
CODE_SIZE = 9  # pt
CODE_LINE_SPACING = 140  # %
CODE_HIGHLIGHT_STYLE = "default"  # Pygments style


def _body(doc: HwpxDocument) -> dict:
    # add_table / add_picture take only the style id from style=, so pass its paragraph shape too.
    return {"style": BODY_STYLE, "para_pr_id_ref": doc.styles.resolve(BODY_STYLE).para_pr_id_ref}


def _set_caption(doc: HwpxDocument, obj, text: str, side: str) -> None:
    style = doc.styles.resolve(CAPTION_STYLE)
    caption = obj.set_caption(text, side=side, char_pr_id_ref=style.char_pr_id_ref)
    for paragraph in caption.paragraphs:
        paragraph.style_id_ref = style.id
        paragraph.para_pr_id_ref = style.para_pr_id_ref


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


def _add_table(doc: HwpxDocument, rows: list[list[str]], aligns: list[str | None], caption: str | None) -> None:
    table = doc.add_table(len(rows), len(rows[0]), **_body(doc))
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


def convert(md_path: Path, template_path: Path, out_path: Path) -> None:
    doc = HwpxDocument.open(template_path)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    paragraph: list[str] = []
    caption: str | None = None
    numbers = {"표": 0, "그림": 0}

    def flush() -> None:
        if paragraph:
            doc.add_paragraph(" ".join(paragraph), style=BODY_STYLE)
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
            doc.add_heading(heading.group(2), level=len(heading.group(1)))
        elif image := _IMAGE.match(line):
            flush()
            image_path = md_path.parent / image.group(1)
            picture = doc.add_picture(image_path.read_bytes(), image_path.suffix.lstrip(".").lower(), **_body(doc))
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
        elif line.strip():
            paragraph.append(line.strip())
        else:
            flush()
        i += 1
    flush()

    doc.save_to_path(out_path)
