"""The Markdown source before it is converted: its front matter, and the checks made on it (the images that
cannot go in, the styles the template lacks), which the command line and web service warn about."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from pathlib import Path
from urllib.parse import unquote

from markdown_it.tree import SyntaxTreeNode

from md2hwpx.images import ImageError, picture_data
from md2hwpx.parser import PARSER, custom_style
from md2hwpx.pieces import marker_lines
from md2hwpx.toc import toc_request

# YAML front matter: a "---" first line (after an optional BOM) up to a "---" or "..." line.
_FRONT_MATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)
# The front matter key that says how captions are numbered (see caption_numbering), and its values.
CAPTION_NUMBERING = "caption-numbering"
CAPTION_NUMBERINGS = ("chapter", "document")
_CAPTION_NUMBERING = re.compile(rf"^{CAPTION_NUMBERING}:[ \t]*(.*?)[ \t]*$", re.MULTILINE)


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


def read_markdown(md_path: Path) -> str:
    """Read *md_path* (UTF-8) without its front matter, which is metadata, not document content."""
    return _FRONT_MATTER.sub("", md_path.read_text(encoding="utf-8"), count=1)


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
