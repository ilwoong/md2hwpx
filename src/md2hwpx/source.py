"""The Markdown source before it is converted: its front matter, and the checks made on it (the images that
cannot go in, the styles the template lacks), which the command line and web service warn about."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from pathlib import Path
from urllib.parse import unquote

from markdown_it.tree import SyntaxTreeNode

from md2hwpx.images import ImageError, picture_data
from md2hwpx.parser import COLUMN_PROBLEMS, PARSER, custom_style
from md2hwpx.pieces import REFERENCE, marker_lines
from md2hwpx.toc import toc_request

# YAML front matter: a "---" first line (after an optional BOM) up to a "---" or "..." line.
_FRONT_MATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)
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


def references(markdown: str) -> list[tuple[int, str]]:
    """(line number, name) of each "[@name]" reference outside code, as the conversion finds them."""
    markdown = _blank_front_matter(markdown)
    lines = markdown.splitlines()
    found = []
    for node in _walk(SyntaxTreeNode(PARSER.parse(markdown))):
        if node.type != "inline":
            continue
        # A table cell's inline content has no lines of its own: its row's are the nearest.
        start, end = next(n.map for n in (node, *_ancestors(node)) if n.map is not None)
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
    found = []
    for node in _walk(SyntaxTreeNode(PARSER.parse(_blank_front_matter(markdown)))):
        if node.type == "container_div" and (name := custom_style(node.info)) not in styles:
            found.append((node.map[0] + 1, name, "본문 스타일로 넣었습니다"))
        elif node.type == "paragraph" and not any(a.type == "footnote" for a in _ancestors(node)):
            # The [TOC] markers as the conversion finds them; a footnote's paragraphs are only text.
            for offset, marker, words in marker_lines(node.children[0]):
                if marker == "toc":
                    found += [
                        (node.map[0] + offset + 1, name, "차례에 모을 수 없습니다")
                        for name in toc_request(words).styles if name not in styles
                    ]
    return found


def column_problems(markdown: str) -> list[tuple[int, str]]:
    """(line number, why) of each "[cols: ...]" comment that sets no column widths, the table after it
    (if any) taking them from its text; "why" is a Korean clause (see parser.COLUMN_PROBLEMS)."""
    env: dict = {}
    PARSER.parse(_blank_front_matter(markdown), env)
    return [
        (line + 1, COLUMN_PROBLEMS[problem].format(values=values, columns=columns))
        for line, problem, values, columns in env.get("column_problems", [])
    ]


def read_markdown(md_path: Path) -> str:
    """Read *md_path* (UTF-8) without its front matter, which is metadata, not document content."""
    return _FRONT_MATTER.sub("", md_path.read_text(encoding="utf-8"), count=1)


def is_draft(md_path: Path) -> bool:
    """Whether *md_path*'s front matter says "draft: true": a file left out of the conversion unless asked for."""
    front = _FRONT_MATTER.match(md_path.read_text(encoding="utf-8"))
    return bool(front and _DRAFT.search(front[0]))


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
