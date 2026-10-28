"""Render mermaid / plantuml fenced code blocks in Markdown to PNG files."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from md2hwpx.parser import PARSER

LANGS = ("mermaid", "plantuml")


class DiagramError(Exception):
    pass


@dataclass(frozen=True)
class DiagramBlock:
    lang: str
    code: str
    start: int  # line of the opening fence (from 0)
    end: int  # line of the closing fence
    prefix: str = ""  # what comes before the fence on its line: indentation, a list marker, "> "


def find_diagram_blocks(markdown: str) -> list[DiagramBlock]:
    """The closed mermaid / plantuml fenced code blocks, found as CommonMark reads them,
    so also inside list items and block quotes."""
    lines = markdown.splitlines()
    blocks = []
    for token in PARSER.parse(markdown):
        info = token.info.split() if token.type == "fence" else []
        if not info or info[0].lower() not in LANGS:
            continue
        start, end = token.map[0], token.map[1] - 1
        # markdown-it runs an unclosed fence to the end of its container; leave those as they are.
        closing = rf"[ \t>]*{re.escape(token.markup[0])}{{{len(token.markup)},}}\s*"
        if end == start or not re.fullmatch(closing, lines[end]):
            continue
        prefix = lines[start][: lines[start].index(token.markup)]
        blocks.append(DiagramBlock(info[0].lower(), token.content, start, end, prefix))
    return blocks


def _run(cmd: list[str], code: str) -> subprocess.CompletedProcess[bytes]:
    if shutil.which(cmd[0]) is None:
        raise DiagramError(f"'{cmd[0]}' not found in PATH")
    return subprocess.run(cmd, input=code.encode("utf-8"), capture_output=True)


def _check(result: subprocess.CompletedProcess[bytes], out_path: Path, tool: str) -> None:
    if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        out_path.unlink(missing_ok=True)
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise DiagramError(f"{tool} failed (exit {result.returncode}): {stderr}")


def render_mermaid(code: str, out_path: Path) -> None:
    result = _run(["mmdc", "-i", "-", "-o", str(out_path)], code)
    _check(result, out_path, "mmdc")


def render_plantuml(code: str, out_path: Path) -> None:
    result = _run(["plantuml", "-tpng", "-pipe"], code)
    # plantuml -pipe writes an error image to stdout on syntax errors, so only save on success.
    if result.returncode == 0:
        out_path.write_bytes(result.stdout)
    _check(result, out_path, "plantuml")


_RENDERERS = {"mermaid": render_mermaid, "plantuml": render_plantuml}


def render_diagrams(markdown: str, out_dir: Path, link_base: Path | None = None) -> str:
    blocks = find_diagram_blocks(markdown)
    if not blocks:
        return markdown
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = markdown.splitlines(keepends=True)
    for block in reversed(blocks):
        digest = hashlib.sha256(block.code.encode("utf-8")).hexdigest()[:12]
        out_path = out_dir / f"{block.lang}-{digest}.png"
        if not out_path.exists():
            _RENDERERS[block.lang](block.code, out_path)
        link = out_path.name if link_base is None else os.path.relpath(out_path, link_base)
        newline = "\n" if lines[block.end].endswith("\n") else ""
        # The prefix keeps the image inside the list item or quote the code block was in.
        lines[block.start : block.end + 1] = [f"{block.prefix}![]({Path(link).as_posix()}){newline}"]
    return "".join(lines)
