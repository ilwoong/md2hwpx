"""The Markdown source before it is converted: its front matter, and the checks made on it (the images that
cannot go in, the styles the template lacks), which the command line and web service warn about."""

from __future__ import annotations

import functools
import re
from collections.abc import Collection, Iterable
from pathlib import Path
from urllib.parse import unquote

from markdown_it.tree import SyntaxTreeNode

from md2hwpx.images import ImageError, picture_data
from md2hwpx.parser import COLUMN_PROBLEMS, PARSER, custom_style, source_lines
from md2hwpx.pieces import MATH_NODES, REFERENCE, equation_script, marker_lines, marker_typo_lines, math_source
from md2hwpx.toc import toc_request

# YAML front matter: a "---" first line (after an optional BOM) up to a "---" or "..." line. The line after the
# first is a "key:" line, or the closing one: a document that opens with a thematic break is not front matter.
_FRONT_MATTER = re.compile(
    r"\A\ufeff?---[ \t]*\r?\n(?=[^\W\d][\w.-]*[ \t]*:(?:[ \t]|\r?$)|(?:---|\.\.\.)[ \t]*\r?$)"
    r".*?^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)",
    re.DOTALL | re.MULTILINE,
)
# The front matter key that says how captions are numbered (see caption_numbering), and its values.
CAPTION_NUMBERING = "caption-numbering"
CAPTION_NUMBERINGS = ("chapter", "document")
_CAPTION_NUMBERING = re.compile(rf"^{CAPTION_NUMBERING}:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
# The front matter line that marks a file as a draft (see is_draft): YAML's true, unquoted.
_DRAFT = re.compile(r"^draft:[ \t]*(?:true|True|TRUE)[ \t]*$", re.MULTILINE)


class ConversionError(Exception):
    """The Markdown cannot be converted (its front matter says what cannot be done), or the converted
    document failed HWPX validation, so it was not saved."""


def _walk(node: SyntaxTreeNode) -> Iterable[SyntaxTreeNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _ancestors(node: SyntaxTreeNode) -> Iterable[SyntaxTreeNode]:
    while node.parent is not None:
        node = node.parent
        yield node


def _in_footnote(node: SyntaxTreeNode) -> bool:
    return any(a.type == "footnote" for a in _ancestors(node))


def _block_lines(node: SyntaxTreeNode) -> tuple[int, int]:
    """The source lines of *node*, from the first up to (not including) the last, or those of the nearest block
    around it: inline content has none of its own, nor has a table cell (its row's are the nearest)."""
    return next(n.map for n in (node, *_ancestors(node)) if n.map is not None)


def _line_of(text: str, node: SyntaxTreeNode, lines: list[str]) -> int:
    """The number of the first of *node*'s source *lines* (see _block_lines) that holds *text*, or of its first
    line if none does."""
    start, end = _block_lines(node)
    return next((i + 1 for i in range(start, end) if text in lines[i]), start + 1)


def _blank_front_matter(markdown: str) -> str:
    """*markdown* with its front matter as blank lines, so line numbers still count it."""
    return _FRONT_MATTER.sub(lambda m: "\n" * m.group(0).count("\n"), markdown, count=1)


@functools.lru_cache(maxsize=2)
def _parsed(markdown: str) -> tuple[SyntaxTreeNode, dict]:
    """*markdown* as the conversion parses it: its tree, and what the parser left in its env. The last ones
    are remembered: each check of a file asks for the same text, and parsing it was most of their time.
    Nothing changes a tree."""
    env: dict = {}
    return SyntaxTreeNode(PARSER.parse(markdown, env)), env


def _nodes(markdown: str) -> Iterable[SyntaxTreeNode]:
    """Every node of *markdown* as the conversion parses it, its front matter as blank lines."""
    return _walk(_parsed(_blank_front_matter(markdown))[0])


def image_paths(markdown: str, base_dir: Path) -> list[tuple[int, Path]]:
    """(line number, path) of each image that goes in as a picture, its path taken from *base_dir*: a
    paragraph's own images (see pieces.paragraph_pieces). One in a heading, table cell, footnote, link or
    emphasis gives only its alt text, and its file is not read."""
    markdown = _blank_front_matter(markdown)
    lines = source_lines(markdown)
    images: list[tuple[int, Path]] = []
    for node in _nodes(markdown):
        if node.type != "paragraph" or _in_footnote(node):
            continue
        for image in (n for n in node.children[0].children if n.type == "image"):
            src = image.attrs["src"]
            # The line of the paragraph that names the image, as written or with its escapes read.
            start, end = node.map
            raw = next((s for s in (src, unquote(src)) if any(s in line for line in lines[start:end])), src)
            images.append((_line_of(raw, node, lines), base_dir / unquote(src)))
    return images


def references(markdown: str) -> list[tuple[int, str]]:
    """(line number, name) of each "[@name]" reference outside code, as the conversion finds them."""
    markdown = _blank_front_matter(markdown)
    lines = source_lines(markdown)
    found = []
    for node in _nodes(markdown):
        if node.type != "inline":
            continue
        start, end = _block_lines(node)
        texts = [n.content for n in _walk(node) if n.type == "text"]
        line_names = [(i + 1, m[1]) for i in range(start, end) for m in REFERENCE.finditer(lines[i])]
        for name in (m[1] for text in texts for m in REFERENCE.finditer(text)):
            # The first line of the block naming it that is not taken yet: a name may come more than once.
            at = next((k for k, (_, n) in enumerate(line_names) if n == name), None)
            found.append(line_names.pop(at) if at is not None else (start + 1, name))
    return found


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


def unknown_styles(markdown: str, styles: Collection[str]) -> list[tuple[int, str, str]]:
    """(line number, name, what follows) of each paragraph style not in *styles* (see convert.paragraph_styles) that
    a custom-style div or a [TOC] marker names. A div's paragraphs are left 본문; a TOC keeps the name, but
    Hancom finds no paragraph of it to collect. "What follows" says which, as a Korean clause."""
    def toc_styles(line: int, words: str) -> list[tuple[int, str, str]]:
        return [(line, name, "차례에 모을 수 없습니다") for name in toc_request(words).styles if name not in styles]

    found = []
    for node in _nodes(markdown):
        if node.type == "container_div" and (name := custom_style(node.info)) not in styles:
            found.append((node.map[0] + 1, name, "본문 스타일로 넣었습니다"))
        elif _in_footnote(node):
            continue
        elif node.type == "hwpx_marker" and node.meta["marker"] == "toc":
            found += toc_styles(node.map[0] + 1, node.meta["words"])
        elif node.type == "paragraph":
            # The [TOC] markers as the conversion finds them; a footnote's paragraphs are only text.
            for offset, marker, words, _ in marker_lines(node.children[0]):
                if marker == "toc":
                    found += toc_styles(node.map[0] + offset + 1, words)
    return found


# What a footnote leaves out (see convert._Writer.add_footnote: it takes its paragraphs only), as a warning names
# it, with its particle.
_FOOTNOTE_LEAVES_OUT = {
    "bullet_list": "목록은", "ordered_list": "목록은", "fence": "코드블록은", "code_block": "코드블록은", "table": "표는",
    "blockquote": "인용문은", "heading": "헤딩은", "hr": "수평선은", "container_div": "스타일 지정 블록은",
    "hwpx_marker": "주석 마커는", "footnote_ref": "각주 참조는", "math_block": "수식 블록은",
}


def footnote_leftovers(markdown: str) -> list[tuple[int, str]]:
    """(line number, what with its particle) of each block in a footnote that is not a paragraph, and each
    footnote reference in a footnote: the footnote goes in without them."""
    markdown = _blank_front_matter(markdown)
    lines = source_lines(markdown)
    found = []
    for note in (n for n in _nodes(markdown) if n.type == "footnote"):
        for block in note.children:
            if block.type in _FOOTNOTE_LEAVES_OUT:
                found.append((block.map[0] + 1, _FOOTNOTE_LEAVES_OUT[block.type]))
            elif block.type == "paragraph":
                for ref in (n for n in _walk(block) if n.type == "footnote_ref"):
                    # The line of the paragraph naming it.
                    line = _line_of(f"[^{ref.meta['label']}]", block, lines)
                    found.append((line, _FOOTNOTE_LEAVES_OUT["footnote_ref"]))
    return sorted(found)


def unsupported_math(markdown: str) -> list[tuple[int, str]]:
    """(line number, the math as written) of each "$...$" or "$$...$$" that cannot be an equation and stays
    text (see pieces.equation_script)."""
    markdown = _blank_front_matter(markdown)
    lines = source_lines(markdown)
    found = []
    for node in (n for n in _nodes(markdown) if n.type in MATH_NODES and equation_script(n.content) is None):
        # The line that holds the math's first line.
        first = node.content.strip().split("\n")[0]
        found.append((_line_of(first, node, lines), math_source(node)))
    return found


def marker_typos(markdown: str) -> list[tuple[int, str]]:
    """(line number, the line) of each line of a paragraph that starts as a marker line ("[새 번호 ...]",
    "[빈 줄 ...]", "\\newpage ...") does but is not one: it stays text (see pieces.marker_typo_lines)."""
    found = []
    for node in _nodes(markdown):
        # A footnote's paragraphs are only text.
        if node.type == "paragraph" and not _in_footnote(node):
            found += [(node.map[0] + offset + 1, line) for offset, line in marker_typo_lines(node.children[0])]
    return found


# The marker lines going away (see old_markers), and the comment marker command that does what each does.
_OLD_MARKERS = {"toc": "차례", "restart": "새번호", "hide_page": "감추기", "blank_lines": "빈줄", "blank_page": "여백면"}


def old_markers(markdown: str) -> list[tuple[int, str, str]]:
    """(line number, the line, the comment marker to write instead) of each marker line going away ("[TOC]",
    "[새 번호 ...]", "[감추기]", "[빈 줄 ...]", "[여백면] ..."): it still works, with a warning. The pandoc ones
    ("\\newpage") stay."""
    found = []
    for node in _nodes(markdown):
        # A footnote's paragraphs are only text.
        if node.type == "paragraph" and not _in_footnote(node):
            for offset, marker, words, line in marker_lines(node.children[0]):
                if marker not in _OLD_MARKERS:
                    continue
                if marker == "blank_page" and re.search(r"\s", words):
                    words = f'"{words}"'
                command = _OLD_MARKERS[marker] + (f" {words}" if words else "")
                found.append((node.map[0] + offset + 1, line, f"<!-- @hwpx {command} -->"))
    return found


def column_problems(markdown: str) -> list[tuple[int, str]]:
    """(line number, why) of each "[cols: ...]" comment that sets no column widths, the table after it
    (if any) taking them from its text; "why" is a Korean clause (see parser.COLUMN_PROBLEMS)."""
    env = _parsed(_blank_front_matter(markdown))[1]
    return [
        (line + 1, COLUMN_PROBLEMS[problem].format(values=values, columns=columns))
        for line, problem, values, columns in env.get("column_problems", [])
    ]


def marker_problems(markdown: str) -> list[tuple[int, str, str]]:
    """(line number, marker, why) of each "<!-- @hwpx ... -->" comment marker left out, in line order: "marker"
    names it ("@hwpx 명령 인자"), "why" is a Korean clause (see parser._markers)."""
    env = _parsed(_blank_front_matter(markdown))[1]
    return sorted((line + 1, marker, why) for line, marker, why in env.get("marker_problems", []))


def read_text(md_path: Path) -> str:
    """The text of *md_path*. Raises ConversionError if it is not UTF-8 (a file saved as CP949, say)."""
    try:
        return md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ConversionError(f"{md_path}: UTF-8 로 저장한 파일이 아닙니다") from None


def read_markdown(md_path: Path) -> str:
    """Read *md_path* (UTF-8) without its front matter, which is metadata, not document content."""
    return _FRONT_MATTER.sub("", read_text(md_path), count=1)


def is_draft(md_path: Path) -> bool:
    """Whether *md_path*'s front matter says "draft: true": a file left out of the conversion unless asked for."""
    front = _FRONT_MATTER.match(read_text(md_path))
    return bool(front and _DRAFT.search(front[0]))


def caption_numbering(md_paths: Iterable[Path]) -> str:
    """How the captions of the document made of *md_paths* are numbered, as their front matter says
    ("caption-numbering: ..."): "chapter" ([표 2-1], within each chapter) or "document" ([표 7]).

    A file that does not say leaves it to the others; if none does, it is "chapter". Raises ConversionError
    for any other value, or for files that say different ones: a document has one way.
    """
    said: dict[str, Path] = {}  # value -> the first file that says it
    for path in md_paths:
        front = _FRONT_MATTER.match(read_text(path))
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
