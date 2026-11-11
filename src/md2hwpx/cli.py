"""Command line entry point: render diagrams, then convert Markdown to HWPX."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from hwpx.errors import HwpxError

from md2hwpx.convert import DEFAULT_TEMPLATE, convert_markdown, paragraph_styles
from md2hwpx.diagram import DiagramError, render_diagrams
from md2hwpx.source import (
    ConversionError, caption_numbering, column_problems, is_draft, read_markdown, references, unknown_styles,
    unusable_images,
)

# Why a "[@name]" reference was left as written (see convert_markdown), as a Korean clause.
_UNRESOLVED = {"missing": "없어", "duplicate": "여럿이라"}
# What convert_input raises for an input it cannot convert. BadZipFile: a template that is not HWPX (a zip).
CONVERSION_ERRORS = (OSError, DiagramError, HwpxError, ConversionError, zipfile.BadZipFile)


def _natural_key(path: Path) -> list:
    # "2.md" before "10.md": digit runs compare as numbers.
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


class NoInputFiles(FileNotFoundError):
    """No file to convert in *path* (see input_files). *reason* says why: "folder" (the folder has no .md file),
    "pattern" (no name matches the patterns) or "draft" (all are drafts); *what* says it, for the message."""

    def __init__(self, path: Path, reason: str, what: str):
        super().__init__(f"{what}: {path}")
        self.path, self.reason, self.what = path, reason, what


def input_files(path: Path, include_draft: bool = False, patterns: Sequence[str] = ()) -> list[Path]:
    """The Markdown file, or every .md file directly in the folder, in name order: only those whose name
    matches one of *patterns* (shell style, "ch_2*.md") if any are given, and drafts (see is_draft) only if
    *include_draft*."""
    if path.is_dir():
        files = sorted((p for p in path.glob("*.md") if p.is_file()), key=_natural_key)
        if not files:
            raise NoInputFiles(path, "folder", "no .md files in folder")
    else:
        files = [path]
    if patterns:
        files = [f for f in files if any(fnmatch.fnmatchcase(f.name, pattern) for pattern in patterns)]
        if not files:
            named = ", ".join(f"'{pattern}'" for pattern in patterns)
            raise NoInputFiles(path, "pattern", f"{named} 에 맞는 .md 파일이 없습니다")
    if include_draft:
        return files
    kept = [f for f in files if not is_draft(f)]
    if not kept:
        raise NoInputFiles(path, "draft", "변환할 .md 파일이 모두 draft 입니다")
    return kept


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


def convert_input(
    input_path: Path, template: Path, output: Path, include_draft: bool = False, patterns: Sequence[str] = (),
    chapter_start: int = 1,
) -> list[ConversionWarning]:
    """Convert the Markdown file, or the .md files of the folder, *input_path* to *output*: those *patterns*
    match, drafts only if *include_draft* (see input_files), the first chapter numbered *chapter_start* in
    captions. Return what the HWPX is made despite (see _warnings)."""
    base_dir = input_path if input_path.is_dir() else input_path.parent
    files = input_files(input_path, include_draft, patterns)
    numbering = caption_numbering(files)
    styles = paragraph_styles(template)
    # A blank line between files keeps the last block of one file from running into the next.
    markdown = "\n\n".join(read_markdown(f) for f in files)
    # Diagram PNGs are embedded in the HWPX, so they only need to live until it is saved.
    with tempfile.TemporaryDirectory() as image_dir:
        markdown = render_diagrams(markdown, out_dir=Path(image_dir), link_base=base_dir)
        unresolved = convert_markdown(markdown, base_dir, template, output, numbering, chapter_start)
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


def _chapter_number(value: str) -> int:
    """A --chapter-start value: a whole number from 1."""
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"1 이상의 정수여야 합니다: {value}")
    return int(value)


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
    parser.add_argument(
        "--include-draft", action="store_true",
        help="front matter 에 draft: true 가 있는 파일도 변환한다 (기본: 빼고 변환)",
    )
    parser.add_argument(
        "--include", action="append", default=[], metavar="PATTERN",
        help="이름이 이 패턴(예: 'ch_2*.md')에 맞는 .md 파일만 변환한다. 여러 번 주면 어느 하나에 맞는 파일. "
        "셸이 펼치지 않게 따옴표로 감싼다",
    )
    parser.add_argument(
        "--chapter-start", type=_chapter_number, default=1, metavar="N",
        help="캡션의 장 번호를 N 부터 센다 (기본: 1). 예: 2 면 첫 장의 표가 [표 2-1]",
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
        warnings = convert_input(args.input, template, output, args.include_draft, args.include, args.chapter_start)
    except CONVERSION_ERRORS as error:
        if isinstance(error, NoInputFiles) and error.reason == "draft":
            error = f"{error.what} (포함하려면 --include-draft): {error.path}"
        print(f"md2hwpx: {error}", file=sys.stderr)
        return 1
    for found in warnings:
        print(f"md2hwpx: 경고: {found}", file=sys.stderr)
    print(output)
    return 0
