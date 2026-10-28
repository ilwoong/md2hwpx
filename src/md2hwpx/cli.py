"""Command line entry point: render diagrams, then convert Markdown to HWPX."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from hwpx.errors import HwpxError

from md2hwpx.convert import convert_markdown
from md2hwpx.diagram import DiagramError, render_diagrams


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="md2hwpx", description="Markdown 파일을 HWPX 파일로 변환한다.")
    parser.add_argument("input", type=Path, help="변환할 Markdown 파일")
    parser.add_argument("-o", "--output", type=Path, help="저장할 HWPX 파일 (기본: 입력 파일 이름에 .hwpx)")
    parser.add_argument(
        "-t", "--template", type=Path, default=Path("template.hwpx"),
        help="템플릿 HWPX 파일 (기본: 현재 폴더의 template.hwpx)",
    )
    args = parser.parse_args(argv)
    output = args.output or args.input.with_suffix(".hwpx")

    try:
        markdown = args.input.read_text(encoding="utf-8")
        # Diagram PNGs are embedded in the HWPX, so they only need to live until it is saved.
        with tempfile.TemporaryDirectory() as image_dir:
            markdown = render_diagrams(markdown, out_dir=Path(image_dir), link_base=args.input.parent)
            convert_markdown(markdown, args.input.parent, args.template, output)
    except (OSError, DiagramError, HwpxError) as error:
        print(f"md2hwpx: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0
