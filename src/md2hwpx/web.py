"""Web service: upload Markdown (files, a zip or a git repository) and download it as HWPX.

Run with: uvicorn md2hwpx.web:app
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from md2hwpx import oauth
from md2hwpx.cli import CONVERSION_ERRORS, convert_input, input_files
from md2hwpx.convert import DEFAULT_TEMPLATE, image_paths

INDEX = Path(__file__).resolve().parent / "static" / "index.html"
MAX_UPLOAD = 50 * 1024 * 1024  # bytes: all uploads of a request, and a zip's extracted files
MAX_ZIP_FILES = 2000
GIT_TIMEOUT = 120  # seconds
# Only http(s), http for repositories on a closed network: file://, ssh and local paths would let a request
# read the server's own files.
_REPO = re.compile(r"https?://[^\s]+")
_REF = re.compile(r"[\w][\w./-]*")
HWPX_TYPE = "application/hwp+zip"
WARNINGS_HEADER = "X-Md2hwpx-Warnings"  # what the HWPX was made despite, one per line

app = FastAPI(title="md2hwpx")
app.include_router(oauth.router)


class _Budget:
    """The bytes a request may still write to disk (MAX_UPLOAD)."""

    def __init__(self) -> None:
        self.left = MAX_UPLOAD

    def copy(self, source, dest: Path) -> None:
        with dest.open("wb") as out:
            while chunk := source.read(1024 * 1024):
                self.left -= len(chunk)
                if self.left < 0:
                    raise HTTPException(413, f"올린 파일이 너무 큽니다 (최대 {MAX_UPLOAD // 1024 // 1024} MB).")
                out.write(chunk)


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root)


def _save_files(files: list[UploadFile], root: Path, budget: _Budget) -> str:
    """Save the uploaded Markdown and image files side by side in *root*; the name of the first .md."""
    if not files:
        raise HTTPException(400, "파일을 선택하세요.")
    for upload in files:
        name = Path(upload.filename or "").name
        if name in ("", ".", ".."):
            raise HTTPException(400, f"파일 이름이 올바르지 않습니다: {upload.filename}")
        budget.copy(upload.file, root / name)
    return next((Path(f.filename).stem for f in files if f.filename.endswith(".md")), "document")


def _zip_name(info: zipfile.ZipInfo) -> str:
    """The entry's name. Without the UTF-8 flag zipfile reads it as cp437, but a zip made on Korean
    Windows holds cp949."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except UnicodeError:
        return info.filename


def _extract_zip(upload: UploadFile | None, root: Path, work: Path, budget: _Budget) -> str:
    """Extract the uploaded zip into *root*, refusing entries that would land outside it."""
    if upload is None or not upload.filename:
        raise HTTPException(400, "zip 파일을 선택하세요.")
    archive = work / "upload.zip"
    budget.copy(upload.file, archive)
    try:
        with zipfile.ZipFile(archive) as zf:
            entries = [info for info in zf.infolist() if not info.is_dir()]
            if len(entries) > MAX_ZIP_FILES:
                raise HTTPException(400, f"zip 안의 파일이 너무 많습니다 (최대 {MAX_ZIP_FILES}개).")
            for info in entries:
                target = root / _zip_name(info)
                if not _inside(target, root):
                    raise HTTPException(400, f"zip 안의 경로가 올바르지 않습니다: {_zip_name(info)}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source:
                    budget.copy(source, target)
    except (zipfile.BadZipFile, RuntimeError):  # RuntimeError: an encrypted entry
        raise HTTPException(400, "zip 파일을 열 수 없습니다 (암호가 걸린 zip 은 쓸 수 없습니다).") from None
    archive.unlink()
    return Path(upload.filename).stem


def _clone(repo: str, ref: str, root: Path, request: Request) -> str:
    """Shallow-clone the http(s) git *repo* (at branch or tag *ref*, if given) into *root*,
    as the user if they are signed in to the server it is on (see oauth)."""
    if not _REPO.fullmatch(repo):
        raise HTTPException(400, "git 저장소 주소는 http:// 또는 https:// 로 시작해야 합니다.")
    if ref and not _REF.fullmatch(ref):
        raise HTTPException(400, f"브랜치·태그 이름이 올바르지 않습니다: {ref}")
    command = [
        "git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.http.allow=always",
        "-c", "core.symlinks=false",
        "clone", "--depth", "1", "--quiet", *(["--branch", ref] if ref else []), "--", repo, str(root),
    ]
    # No prompt for a user name: a private repository fails instead of waiting.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    server = oauth.server_for(repo)
    auth = oauth.git_header(request, server) if server else None
    if auth:
        # In the environment, not the command line, which other processes can list. Only for this server's
        # address (server_for): a token sent to any address given would be handed to whoever runs it.
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=auth)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise HTTPException(400, f"git 저장소를 {GIT_TIMEOUT}초 안에 받지 못했습니다.") from None
    if result.returncode != 0:
        hint = f"\n비공개 저장소라면 {server.provider.label} 에 로그인하세요." if server and not auth else ""
        raise HTTPException(400, f"git 저장소를 받지 못했습니다: {result.stderr.strip()}{hint}")
    shutil.rmtree(root / ".git", ignore_errors=True)
    return repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "document"


def _remove_symlinks(root: Path) -> None:
    """Drop symbolic links, which could point the conversion at the server's own files."""
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder) / name
            if path.is_symlink():
                path.unlink()


def _input_path(root: Path, path: str, name: str) -> tuple[Path, str]:
    """The Markdown file or folder to convert, and the name of the HWPX to save.

    *path* is relative to *root*. Without it, the root's only .md file, or else the root folder (its .md
    files in name order, as the command line takes a folder).
    """
    if path:
        target = root / path
        if not _inside(target, root) or not target.exists():
            raise HTTPException(400, f"변환할 경로가 없습니다: {path}")
        target = target.resolve()
        return target, (target.stem if target.is_file() else target.name)
    entries = list(root.iterdir())
    # A zip of a folder holds that folder: look inside it.
    if len(entries) == 1 and entries[0].is_dir():
        root = entries[0]
    markdown = sorted(root.glob("*.md"))
    if len(markdown) == 1:
        return markdown[0], markdown[0].stem
    return root, name


def _check_images(input_path: Path, root: Path) -> None:
    """Refuse images outside *root*: "../x.png" or "/etc/x" would put the server's files in the HWPX."""
    try:
        files = input_files(input_path)
    except FileNotFoundError:
        raise HTTPException(400, f"변환할 .md 파일이 없습니다: {_relative(input_path, root) or '/'}") from None
    for md in files:
        for line, image in image_paths(md.read_text(encoding="utf-8"), md.parent):
            if not _inside(image, root):
                raise HTTPException(400, f"{_relative(md, root)}:{line}: 업로드한 폴더 밖의 이미지는 쓸 수 없습니다")


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path != root else ""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX.read_text(encoding="utf-8")


@app.post("/convert")
def convert(
    request: Request,
    source: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    archive: UploadFile | None = File(default=None),
    repo: str = Form(default=""),
    ref: str = Form(default=""),
    path: str = Form(default=""),
    template: UploadFile | None = File(default=None),
) -> Response:
    """Convert the uploaded *source* ("files", "zip" or "git") and send the HWPX back."""
    budget = _Budget()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp).resolve()
        root = work / "input"
        root.mkdir()
        if source == "files":
            name = _save_files(files, root, budget)
        elif source == "zip":
            name = _extract_zip(archive, root, work, budget)
        elif source == "git":
            name = _clone(repo.strip(), ref.strip(), root, request)
        else:
            raise HTTPException(400, f"알 수 없는 입력 방식입니다: {source}")
        _remove_symlinks(root)
        input_path, name = _input_path(root, path.strip().strip("/"), name)
        _check_images(input_path, root)

        template_path = DEFAULT_TEMPLATE
        if template is not None and template.filename:
            template_path = work / "template.hwpx"
            budget.copy(template.file, template_path)
        output = work / "output.hwpx"
        try:
            found = convert_input(input_path, template_path, output)
        except CONVERSION_ERRORS as error:
            # Paths in the message are the request's temporary folder: show them from the upload's root.
            raise HTTPException(400, str(error).replace(f"{root}/", "").replace(f"{work}/", "")) from None
        data = output.read_bytes()
        # Every image is inside root (_check_images): name them from there.
        warnings = [
            str(w._replace(file=_relative(w.file, root), path=w.path and _relative(w.path.resolve(), root)))
            for w in found
        ]
    filename = f"{name}.hwpx"
    disposition = f"attachment; filename=\"document.hwpx\"; filename*=UTF-8''{quote(filename)}"
    headers = {"Content-Disposition": disposition}
    if warnings:
        # A header holds Latin-1 only: the page decodes it (decodeURIComponent).
        headers[WARNINGS_HEADER] = quote("\n".join(warnings))
    return Response(data, media_type=HWPX_TYPE, headers=headers)
