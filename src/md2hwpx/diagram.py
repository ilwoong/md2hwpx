"""Render mermaid / plantuml fenced code blocks in Markdown to PNG files."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

LANGS = ("mermaid", "plantuml")

_OPEN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class DiagramError(Exception):
    pass


@dataclass(frozen=True)
class DiagramBlock:
    lang: str
    code: str
    start: int
    end: int


def find_diagram_blocks(markdown: str) -> list[DiagramBlock]:
    lines = markdown.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        m = _OPEN_FENCE.match(lines[i])
        if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
            i += 1
            continue
        fence = m.group(1)
        close = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j])), None)
        if end is None:
            i += 1
            continue
        info = m.group(2).split()
        lang = info[0].lower() if info else ""
        if lang in LANGS:
            code = "\n".join(lines[i + 1 : end]) + "\n"
            blocks.append(DiagramBlock(lang, code, i, end))
        i = end + 1
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
        lines[block.start : block.end + 1] = [f"![]({Path(link).as_posix()}){newline}"]
    return "".join(lines)
