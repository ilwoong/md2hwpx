"""Command line entry point: render diagrams, then convert Markdown to HWPX."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from hwpx.errors import HwpxError

from md2hwpx.convert import ConversionError, convert_markdown, missing_images, read_markdown
from md2hwpx.diagram import DiagramError, render_diagrams


def _natural_key(path: Path) -> list:
    # "2.md" before "10.md": digit runs compare as numbers.
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _input_files(path: Path) -> list[Path]:
    """The Markdown file, or every .md file directly in the folder, in name order."""
    if not path.is_dir():
        return [path]
    files = sorted((p for p in path.glob("*.md") if p.is_file()), key=_natural_key)
    if not files:
        raise FileNotFoundError(f"no .md files in folder: {path}")
    return files


class MissingImagesError(Exception):
    """Images the Markdown files name but are not there, as (file, line, image) in *missing*."""

    def __init__(self, missing: list[tuple[Path, int, Path]]):
        super().__init__("\n".join(_missing_image_message(*m) for m in missing))
        self.missing = missing


def _missing_image_message(file: Path, line: int, image: Path) -> str:
    return f"{file}:{line}: 이미지 파일이 없습니다: {image}"


def convert_input(input_path: Path, template: Path, output: Path) -> None:
    """Convert the Markdown file, or the .md files of the folder, *input_path* to *output*.

    Every missing image is reported (MissingImagesError) before anything is converted.
    """
    base_dir = input_path if input_path.is_dir() else input_path.parent
    files = _input_files(input_path)
    missing = [
        (f, line, image)
        for f in files
        for line, image in missing_images(f.read_text(encoding="utf-8"), f.parent)
    ]
    if missing:
        raise MissingImagesError(missing)
    # A blank line between files keeps the last block of one file from running into the next.
    markdown = "\n\n".join(read_markdown(f) for f in files)
    # Diagram PNGs are embedded in the HWPX, so they only need to live until it is saved.
    with tempfile.TemporaryDirectory() as image_dir:
        markdown = render_diagrams(markdown, out_dir=Path(image_dir), link_base=base_dir)
        convert_markdown(markdown, base_dir, template, output)


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
        "-t", "--template", type=Path, default=Path("template.hwpx"),
        help="템플릿 HWPX 파일 (기본: 현재 폴더의 template.hwpx)",
    )
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)

    try:
        output = _output_path(args.input, args.output)
        convert_input(args.input, args.template, output)
    except MissingImagesError as error:
        for missing in error.missing:
            print(f"md2hwpx: {_missing_image_message(*missing)}", file=sys.stderr)
        return 1
    except (OSError, DiagramError, HwpxError, ConversionError) as error:
        print(f"md2hwpx: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0
