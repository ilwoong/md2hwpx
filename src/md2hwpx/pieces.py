"""A Markdown paragraph as the converter takes it: inline text as runs of one look, and the paragraph cut
into pieces at its images and at the lines that are this project's markers ([TOC], \\newpage, ...)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from markdown_it.tree import SyntaxTreeNode

# "[표] ..." / "[그림] ..." right before a table or code block / image is its caption,
# written as "[표 N] ..." / "[그림 N] ..." numbered in document order.
_CAPTION = re.compile(r"^\[(표|그림)\][ \t]*")
# Lines of their own in a paragraph: table of contents, page break as pandoc writes it, number restart,
# page hiding and blank page markers.
MARKERS = {
    # "[TOC]", or "[TOC 그림 표 제목]": the lists to make, in that order. "링크없음" leaves out the links.
    # The 제목 list collects 개요 paragraphs, or with 스타일="A;B" those styles' (and with "개요" both).
    "toc": re.compile(
        r'\[TOC((?:[ \t]+(?:제목|개요|표|그림|수식|링크없음|스타일="[^"\n]+"))*)[ \t]*\]', re.IGNORECASE
    ),
    "page_break": re.compile(r"\\(?:newpage|pagebreak)[ \t]*"),
    # "[새 번호 쪽 표 그림 수식]": those numbers start over at 1 from the next paragraph (Hancom's 새 번호로 시작).
    "restart": re.compile(r"\[새 번호((?:[ \t]+(?:쪽|표|그림|수식))+)[ \t]*\]"),
    # "[감추기]": the next paragraph's page shows no page number, header or footer (Hancom's 감추기).
    "hide_page": re.compile(r"\[감추기\]"),
    # "[여백면]" or "[여백면] 문구": a page kept as margin, with that text centred on it.
    "blank_page": re.compile(r"\[\s*여백면\s*\]\s*(.*)"),
    # "[빈 줄]" or "[빈 줄 3]": that many empty paragraphs (one without a number), at most 10.
    "blank_lines": re.compile(r"\[빈 줄((?:[ \t]+(?:10|\d))?)[ \t]*\]"),
}
_MARKS = {"strong": "bold", "em": "italic", "s": "strike"}


class Run(NamedTuple):
    """Inline text with one look, or a footnote reference (*note*, with no text)."""

    text: str
    marks: frozenset[str] = frozenset()  # bold, italic, strike, code
    url: str | None = None  # link target
    note: int | None = None  # footnote id


class Piece(NamedTuple):
    """Part of a paragraph: text "runs", an "image", or a marker line (a key of MARKERS)."""

    kind: str
    runs: list[Run] | None = None
    image: SyntaxTreeNode | None = None
    text: str = ""  # a blank page's wording, or the TOC or restart marker's words (see MARKERS)


def inline_runs(
    nodes: Iterable[SyntaxTreeNode], marks: frozenset[str] = frozenset(), url: str | None = None
) -> list[Run]:
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
            runs += inline_runs(node.children, marks | {_MARKS[node.type]}, url)
        elif node.type == "link":
            runs += inline_runs(node.children, marks, node.attrs["href"])
        elif node.type == "image":
            runs += inline_runs(node.children, marks, url)
        elif node.type == "footnote_ref":
            runs.append(Run("", marks, url, note=node.meta["id"]))
    return runs


def plain_text(runs: list[Run]) -> str:
    return "".join(run.text for run in runs)


def _is_blank(run: Run) -> bool:
    return run.note is None and not run.text.strip()


def strip_runs(runs: list[Run]) -> list[Run]:
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


def block_kind(node: SyntaxTreeNode | None) -> str | None:
    """The block's type, or "image" for a paragraph that starts with an image (a caption may precede it)."""
    if node is None:
        return None
    if node.type == "paragraph":
        first = next((c for c in node.children[0].children if not (c.type == "text" and not c.content.strip())), None)
        if first is not None and first.type == "image":
            return "image"
    return node.type


def caption_line(runs: list[Run]) -> tuple[str, list[Run]] | None:
    """("표" or "그림", the title's runs) if *runs* is a "[표] ..." / "[그림] ..." caption line, else None."""
    first = runs[0]
    match = _CAPTION.match(first.text) if first == Run(first.text) else None
    if match is None:
        return None
    return match[1], [first._replace(text=first.text[match.end() :]), *runs[1:]]


def _lines(inline: SyntaxTreeNode) -> list[tuple[int, list[SyntaxTreeNode]]]:
    """A paragraph's inline content line by line (split at its soft breaks), each line with its offset in
    the paragraph's source lines: a hard break ends a source line too, though it stays in its line."""
    lines: list[tuple[int, list[SyntaxTreeNode]]] = [(0, [])]
    offset = 0
    for child in inline.children:
        if child.type in ("softbreak", "hardbreak"):
            offset += 1
        if child.type == "softbreak":
            lines.append((offset, []))
        else:
            lines[-1][1].append(child)
    return lines


def _marker(line: list[SyntaxTreeNode]) -> tuple[str, str] | None:
    """(marker, its words) if *line* holds only a marker (a key of MARKERS), else None.

    Only plain text is: a line with emphasis, a link or a break in it is text, whatever it reads.
    """
    text = line[0].content.strip() if len(line) == 1 and line[0].type == "text" else ""
    for name, pattern in MARKERS.items():
        if match := pattern.fullmatch(text):
            # The blank page marker carries a wording, the TOC and restart markers their words and the blank
            # lines marker its count, in their one group.
            return name, match.group(1).strip() if match.groups() else ""
    return None


def marker_lines(inline: SyntaxTreeNode) -> list[tuple[int, str, str]]:
    """(source line offset in the paragraph, marker, its words) of each marker line of a paragraph's inline
    content, as paragraph_pieces finds them."""
    return [(offset, *found) for offset, line in _lines(inline) if (found := _marker(line))]


def paragraph_pieces(inline: SyntaxTreeNode) -> list[Piece]:
    """Split a paragraph's inline content into pieces: each image, and each line holding only
    a marker, stands apart; the text between them is a "runs" piece (blank ones are dropped)."""
    pieces: list[Piece] = []
    runs: list[Run] = []

    def flush() -> None:
        if not all(_is_blank(run) for run in runs):
            pieces.append(Piece("runs", strip_runs(runs)))
        runs.clear()

    for _, line in _lines(inline):
        found = _marker(line)
        if found:
            flush()
            marker, words = found
            pieces.append(Piece(marker, text=words))
            continue
        if runs:
            runs.append(Run(" "))  # the line break between two lines of text
        for child in line:
            if child.type == "image":
                flush()
                pieces.append(Piece("image", image=child))
            else:
                runs += inline_runs([child])
    flush()
    return pieces
