#!/usr/bin/env python3
"""Convert Markdown to HWPX on an md2hwpx web service, sending the images it names along with it:
    md2hwpx-remote.py INPUT [-o OUTPUT] [-t TEMPLATE] [--include-draft] [--include PATTERN ...] [--chapter-start N]
                      [--server URL]

INPUT is a Markdown file, or a folder whose .md files are joined in name order, as md2hwpx itself takes.
The Markdown and the image files it names are zipped as they lie on disk, so "images/a.png" and "../a.png"
mean on the server what they mean here, and the zip is sent to the service's POST /convert.

Copy this one file anywhere and run it: it needs Python 3.9 or later and nothing else. The service is
http://localhost:8000, or MD2HWPX_SERVER, or --server.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote

TIMEOUT = 600  # seconds for the service to answer: a repository of diagrams takes minutes

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
# "![alt](path)", "![alt](path "title")", "![alt](<path with spaces>)"
_IMAGE = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^\s)]+)")
# "[name]: path", which "![alt][name]" may name. It may be a link's instead, sent along if it is a file.
_DEFINITION = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
# "http://...", "data:...", "//host/..." and "#part" name no file here.
_NOT_A_FILE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:|//|#")


def named_files(markdown: str, folder: Path) -> tuple[list[Path], list[Path]]:
    """The files *markdown* names, their paths taken from *folder*: (images, link definitions).

    Code blocks and code spans are left out. This reads less closely than the service does: what it takes
    for an image that is not one costs a file sent for nothing, and what it misses comes back as the
    service's warning that the image is missing."""
    images: list[Path] = []
    definitions: list[Path] = []
    fence = ""
    for line in markdown.splitlines():
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1).startswith(fence) and not line.strip().strip(fence[0]):
                fence = ""
            continue
        if opened:
            fence = opened.group(1)
            continue
        line = _CODE_SPAN.sub("", line)
        definition = _DEFINITION.match(line)
        for found, target in [(images, m.group(1)) for m in _IMAGE.finditer(line)] + (
            [(definitions, definition.group(1))] if definition else []
        ):
            target = target[1:-1] if target.startswith("<") else target
            if target and not _NOT_A_FILE.match(target) and not os.path.isabs(target):
                found.append(Path(os.path.normpath(folder / unquote(target))))
    return images, definitions


def collect(input_path: Path) -> tuple[Path, list[Path], str]:
    """What to send for *input_path*: (the folder the zip is made from, the files in it, the path of
    *input_path* in the zip, "" if it is that folder).

    The folder is the nearest one holding the Markdown and every image it names, also an image that is
    not there: the service refuses an image outside what was sent, and only warns of one that is missing."""
    input_path = Path(os.path.abspath(input_path))
    if input_path.is_dir():
        sources = sorted(p for p in input_path.glob("*.md") if p.is_file())
    else:
        sources = [input_path]
    files = list(sources)
    inside = [input_path]
    for source in sources:
        images, definitions = named_files(source.read_text(encoding="utf-8"), source.parent)
        inside += images
        files += [p for p in images + definitions if p.is_file()]
    root = Path(os.path.commonpath([p if p.is_dir() else p.parent for p in inside]))
    files = sorted({p for p in files if p.is_relative_to(root)})
    return root, files, "" if input_path == root else input_path.relative_to(root).as_posix()


def make_zip(root: Path, files: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    """The body of a multipart/form-data request, and its Content-Type."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, data) in files.items():
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        parts.append(head.encode() + b"Content-Type: application/octet-stream\r\n\r\n" + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def output_path(input_path: Path, output: str | None) -> Path:
    """Where to save, as md2hwpx does: "<input name>.hwpx" in *output* if it is a folder (or the current
    folder if it is omitted), else *output*; numbered "-1", "-2", ... so no existing file is overwritten.
    An *output* ending in "/" is a folder, made with its parents if it is not there yet."""
    name = (input_path.resolve().name if input_path.is_dir() else input_path.stem) + ".hwpx"
    if output is None:
        path = Path(name)
    else:
        path = Path(output)
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
    parser = argparse.ArgumentParser(
        prog="md2hwpx-remote.py",
        description="Markdown 과 그 문서가 쓰는 이미지를 zip 으로 묶어 md2hwpx 웹 서비스에 보내고 HWPX 를 받습니다.",
    )
    parser.add_argument("input", type=Path, help="Markdown 파일, 또는 .md 파일이 든 폴더 (이름 순으로 이어 붙임)")
    parser.add_argument("-o", "--output", help="저장할 HWPX 파일 또는 폴더 (기본: 현재 폴더의 <입력 이름>.hwpx)")
    parser.add_argument("-t", "--template", type=Path, help="템플릿 HWPX 파일 (기본: 서비스의 내장 템플릿)")
    parser.add_argument("--include-draft", action="store_true", help="draft: true 인 파일도 변환")
    parser.add_argument("--include", action="append", default=[], metavar="PATTERN",
                        help="폴더에서 이름이 패턴에 맞는 .md 파일만 변환 (여러 번 쓸 수 있음)")
    parser.add_argument("--chapter-start", metavar="N", help="캡션의 첫 장 번호 (기본: 1)")
    parser.add_argument("--server", default=os.environ.get("MD2HWPX_SERVER") or "http://localhost:8000",
                        help="웹 서비스 주소 (기본: MD2HWPX_SERVER 또는 http://localhost:8000)")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"md2hwpx-remote: 입력이 없습니다: {args.input}", file=sys.stderr)
        return 1
    if args.template and not args.template.is_file():
        print(f"md2hwpx-remote: 템플릿 파일이 없습니다: {args.template}", file=sys.stderr)
        return 1
    root, files, path = collect(args.input)
    fields = {"source": "zip", "path": path, "include": ",".join(args.include),
              "include_draft": "true" if args.include_draft else "false", "chapter_start": args.chapter_start or ""}
    uploads = {"archive": ("input.zip", make_zip(root, files))}
    if args.template:
        uploads["template"] = ("template.hwpx", args.template.read_bytes())
    body, content_type = _multipart(fields, uploads)
    request = urllib.request.Request(args.server.rstrip("/") + "/convert", data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read()
            warnings = unquote(response.headers.get("X-Md2hwpx-Warnings", ""))
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error)["detail"]
        except (ValueError, KeyError, TypeError):
            detail = f"HTTP {error.code}"
        print(f"md2hwpx-remote: {detail}", file=sys.stderr)
        return 1
    except OSError as error:  # URLError, a timeout
        print(f"md2hwpx-remote: 웹 서비스에 연결하지 못했습니다 ({args.server}): {error}", file=sys.stderr)
        return 1
    output = output_path(args.input, args.output)
    output.write_bytes(data)
    for warning in filter(None, warnings.split("\n")):
        print(f"md2hwpx-remote: 경고: {warning}", file=sys.stderr)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
