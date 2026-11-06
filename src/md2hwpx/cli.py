"""Command line entry point: render diagrams, then convert Markdown to HWPX."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import NamedTuple

from hwpx.errors import HwpxError

from md2hwpx.convert import DEFAULT_TEMPLATE, convert_markdown, paragraph_styles
from md2hwpx.diagram import DiagramError, render_diagrams
from md2hwpx.source import (
    ConversionError, caption_numbering, column_problems, read_markdown, references, unknown_styles,
    unusable_images,
)

# Why a "[@name]" reference was left as written (see convert_markdown), as a Korean clause.
_UNRESOLVED = {"missing": "없어", "duplicate": "여럿이라"}
# What convert_input raises for an input it cannot convert. BadZipFile: a template that is not HWPX (a zip).
CONVERSION_ERRORS = (OSError, DiagramError, HwpxError, ConversionError, zipfile.BadZipFile)


def _natural_key(path: Path) -> list:
    # "2.md" before "10.md": digit runs compare as numbers.
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def input_files(path: Path) -> list[Path]:
    """The Markdown file, or every .md file directly in the folder, in name order."""
    if not path.is_dir():
        return [path]
    files = sorted((p for p in path.glob("*.md") if p.is_file()), key=_natural_key)
    if not files:
        raise FileNotFoundError(f"no .md files in folder: {path}")
    return files


class ConversionWarning(NamedTuple):
    """Something the HWPX is made despite: *message*, at *line* of *file*, about *path* (an image) if any."""

    file: Path | str
    line: int
    message: str
    path: Path | str | None = None

    def __str__(self) -> str:
        """"<file>:<line>: <message>", and ": <path>" after it if the warning is about a file."""
        return f"{self.file}:{self.line}: {self.message}" + (f": {self.path}" if self.path is not None else "")


def _warnings(md: Path, styles: set[str], unresolved: dict[str, str]) -> list[ConversionWarning]:
    """What the HWPX is made despite in *md*, in line order: images shown as a placeholder (see
    unusable_images), styles the template lacks that custom-style divs or [TOC] name (see unknown_styles),
    "[cols: ...]" comments that set no widths (see column_problems) and the references left as written, *unresolved* (see convert_markdown)."""
    markdown = md.read_text(encoding="utf-8")
    found = [
        ConversionWarning(md, line, f"{why} 대체 이미지를 넣었습니다", image)
        for line, image, why in unusable_images(markdown, md.parent)
    ]
    found += [
        ConversionWarning(md, line, f"템플릿에 '{name}' 문단 스타일이 없어 {result}")
        for line, name, result in unknown_styles(markdown, styles)
    ]
    found += [
        ConversionWarning(md, line, f"열 너비 지정 [cols: …] 은 {why} 무시했습니다")
        for line, why in column_problems(markdown)
    ]
    found += [
        ConversionWarning(
            md, line, f"'#{name}' 이름의 표·그림이 {_UNRESOLVED[unresolved[name]]} [@{name}] 를 그대로 두었습니다"
        )
        for line, name in references(markdown) if name in unresolved
    ]
    return sorted(found, key=lambda w: w.line)


def convert_input(input_path: Path, template: Path, output: Path) -> list[ConversionWarning]:
    """Convert the Markdown file, or the .md files of the folder, *input_path* to *output*, and return
    what the HWPX is made despite (see _warnings)."""
    base_dir = input_path if input_path.is_dir() else input_path.parent
    files = input_files(input_path)
    numbering = caption_numbering(files)
    styles = paragraph_styles(template)
    # A blank line between files keeps the last block of one file from running into the next.
    markdown = "\n\n".join(read_markdown(f) for f in files)
    # Diagram PNGs are embedded in the HWPX, so they only need to live until it is saved.
    with tempfile.TemporaryDirectory() as image_dir:
        markdown = render_diagrams(markdown, out_dir=Path(image_dir), link_base=base_dir)
        unresolved = convert_markdown(markdown, base_dir, template, output, numbering)
    return [w for f in files for w in _warnings(f, styles, unresolved)]


def _output_path(input_path: Path, output: str | None) -> Path:
    """Where to save: "<input name>.hwpx" in *output* if it is a folder (or the current folder
    if it is omitted), else *output*; numbered "-1", "-2", ... so no existing file is overwritten.
    An *output* ending in "/" is a folder, made with its parents if it is not there yet."""
    name = (input_path.resolve().name if input_path.is_dir() else input_path.stem) + ".hwpx"
    if output is None:
        path = Path(name)
    else:
        path = Path(output)
        # Path drops the trailing "/", so it is read off the argument as given.
        if output.endswith(("/", os.sep)):
            path.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            path = path / name
    candidate, number = path, 0
    while candidate.exists():
        number += 1
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="md2hwpx", description="Markdown 파일을 HWPX 파일로 변환한다.")
    parser.add_argument("input", type=Path, help="변환할 Markdown 파일, 또는 .md 파일들이 있는 폴더")
    parser.add_argument(
        "-o", "--output",
        help="저장할 HWPX 파일 또는 폴더 (기본: 현재 폴더의 '입력 이름.hwpx'). 끝이 / 이면 폴더이고, 없으면 만든다. "
        "파일이 있으면 이름 뒤에 -1, -2 ... 를 붙인다",
    )
    parser.add_argument(
        "-t", "--template", type=Path,
        help="템플릿 HWPX 파일 (기본: 현재 폴더의 template.hwpx, 없으면 내장 템플릿)",
    )
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    template = args.template or (Path("template.hwpx") if Path("template.hwpx").is_file() else DEFAULT_TEMPLATE)

    try:
        output = _output_path(args.input, args.output)
        warnings = convert_input(args.input, template, output)
    except CONVERSION_ERRORS as error:
        print(f"md2hwpx: {error}", file=sys.stderr)
        return 1
    for found in warnings:
        print(f"md2hwpx: 경고: {found}", file=sys.stderr)
    print(output)
    return 0
