"""Command line entry point: render diagrams, then convert Markdown to HWPX."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from hwpx.errors import HwpxError

from md2hwpx.convert import (
    DEFAULT_TEMPLATE, ConversionError, caption_numbering, convert_markdown, paragraph_styles, read_markdown,
    unknown_styles, unusable_images,
)
from md2hwpx.diagram import DiagramError, render_diagrams

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


def warning(file: Path | str, line: int, message: str, path: Path | str | None = None) -> str:
    """"<file>:<line>: <message>", and ": <path>" after it if the warning is about a file (an image)."""
    return f"{file}:{line}: {message}" + (f": {path}" if path is not None else "")


def _warnings(md: Path, styles: set[str]) -> list[tuple[Path, int, str, Path | None]]:
    """What the HWPX is made despite in *md*, in line order: images shown as a placeholder (see
    unusable_images) and custom-style divs whose style the template lacks (see unknown_styles)."""
    markdown = md.read_text(encoding="utf-8")
    found = [(md, line, f"{why} 대체 이미지를 넣었습니다", image) for line, image, why in unusable_images(markdown, md.parent)]
    found += [
        (md, line, f"템플릿에 '{name}' 문단 스타일이 없어 본문 스타일로 넣었습니다", None)
        for line, name in unknown_styles(markdown, styles)
    ]
    return sorted(found, key=lambda w: w[1])


def convert_input(input_path: Path, template: Path, output: Path) -> list[tuple[Path, int, str, Path | None]]:
    """Convert the Markdown file, or the .md files of the folder, *input_path* to *output*.

    What the HWPX is made despite (see _warnings) is returned as (file, line, message, path or None),
    the arguments of warning.
    """
    base_dir = input_path if input_path.is_dir() else input_path.parent
    files = input_files(input_path)
    numbering = caption_numbering(files)
    styles = paragraph_styles(template)
    warnings = [w for f in files for w in _warnings(f, styles)]
    # A blank line between files keeps the last block of one file from running into the next.
    markdown = "\n\n".join(read_markdown(f) for f in files)
    # Diagram PNGs are embedded in the HWPX, so they only need to live until it is saved.
    with tempfile.TemporaryDirectory() as image_dir:
        markdown = render_diagrams(markdown, out_dir=Path(image_dir), link_base=base_dir)
        convert_markdown(markdown, base_dir, template, output, numbering)
    return warnings


def _output_path(input_path: Path, output: Path | None) -> Path:
    """Where to save: "<input name>.hwpx" in *output* if it is a folder (or the current folder
    if it is omitted), else *output*; numbered "-1", "-2", ... so no existing file is overwritten."""
    name = (input_path.resolve().name if input_path.is_dir() else input_path.stem) + ".hwpx"
    if output is None:
        output = Path(name)
    elif output.is_dir():
        output = output / name
    candidate, number = output, 0
    while candidate.exists():
        number += 1
        candidate = output.with_name(f"{output.stem}-{number}{output.suffix}")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="md2hwpx", description="Markdown 파일을 HWPX 파일로 변환한다.")
    parser.add_argument("input", type=Path, help="변환할 Markdown 파일, 또는 .md 파일들이 있는 폴더")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="저장할 HWPX 파일 또는 폴더 (기본: 현재 폴더의 '입력 이름.hwpx'). 파일이 있으면 이름 뒤에 -1, -2 ... 를 붙인다",
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
        print(f"md2hwpx: 경고: {warning(*found)}", file=sys.stderr)
    print(output)
    return 0
