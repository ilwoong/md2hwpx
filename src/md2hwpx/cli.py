"""Command line entry point: render diagrams, then convert Markdown to HWPX."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from hwpx.errors import HwpxError

from md2hwpx.convert import convert_markdown
from md2hwpx.diagram import DiagramError, render_diagrams


def _natural_key(path: Path) -> list:
    # "2.md" before "10.md": digit runs compare as numbers.
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _read_input(path: Path) -> str:
    """The Markdown of a file, or of every .md file directly in a folder, joined in name order."""
    if not path.is_dir():
        return path.read_text(encoding="utf-8")
    files = sorted((p for p in path.glob("*.md") if p.is_file()), key=_natural_key)
    if not files:
        raise FileNotFoundError(f"no .md files in folder: {path}")
    # A blank line between files keeps the last block of one file from running into the next.
    return "\n\n".join(f.read_text(encoding="utf-8") for f in files)


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
    base_dir = args.input if args.input.is_dir() else args.input.parent

    try:
        markdown = _read_input(args.input)
        output = _output_path(args.input, args.output)
        # Diagram PNGs are embedded in the HWPX, so they only need to live until it is saved.
        with tempfile.TemporaryDirectory() as image_dir:
            markdown = render_diagrams(markdown, out_dir=Path(image_dir), link_base=base_dir)
            convert_markdown(markdown, base_dir, args.template, output)
    except (OSError, DiagramError, HwpxError) as error:
        print(f"md2hwpx: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0
