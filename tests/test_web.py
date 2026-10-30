import io
import os
import struct
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from hwpx import HwpxDocument

from md2hwpx import web
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE

client = TestClient(web.app)


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _hwpx(response, tmp_path: Path) -> HwpxDocument:
    assert response.status_code == 200, response.text
    out = tmp_path / "out.hwpx"
    out.write_bytes(response.content)
    return HwpxDocument.open(out)


def _filename(response) -> str:
    return unquote(response.headers["content-disposition"].split("filename*=UTF-8''")[1])


def _error(response, status: int = 400) -> str:
    assert response.status_code == status, response.text
    return response.json()["detail"]


def test_index_page():
    response = client.get("/")
    assert response.status_code == 200
    assert 'action' not in response.text and 'name="source"' in response.text


def test_files_with_image(tmp_path):
    files = [
        ("files", ("보고서.md", "# 제목\n\n본문\n\n![그림](a.png)\n".encode(), "text/markdown")),
        ("files", ("a.png", _png(), "image/png")),
    ]
    response = client.post("/convert", data={"source": "files"}, files=files)

    assert response.headers["content-type"] == "application/hwp+zip"
    assert _filename(response) == "보고서.hwpx"
    doc = _hwpx(response, tmp_path)
    assert [p.text for p in doc.paragraphs if p.text][:2] == ["제목", "본문"]
    assert len(doc.media.picture_references()) == 1
    assert "x-md2hwpx-warnings" not in response.headers


def test_files_missing_image_is_a_placeholder_and_a_warning(tmp_path):
    files = [("files", ("doc.md", b"text\n\n![](missing.png)\n\n![](img/gone.png)\n", "text/markdown"))]
    response = client.post("/convert", data={"source": "files"}, files=files)

    doc = _hwpx(response, tmp_path)
    assert len(doc.media.picture_references()) == 2
    # Named from the upload's root, one per line, URL-encoded as a header holds Latin-1 only.
    assert unquote(response.headers["x-md2hwpx-warnings"]).splitlines() == [
        "doc.md:3: 이미지 파일이 없어 대체 이미지를 넣었습니다: missing.png",
        "doc.md:5: 이미지 파일이 없어 대체 이미지를 넣었습니다: img/gone.png",
    ]


@pytest.mark.parametrize("src", ["../secret.png", "/etc/hostname", "%2E%2E/secret.png"])
def test_images_outside_the_upload_are_refused(src):
    files = [("files", ("doc.md", f"![]({src})\n".encode(), "text/markdown"))]
    detail = _error(client.post("/convert", data={"source": "files"}, files=files))
    assert detail == "doc.md:1: 업로드한 폴더 밖의 이미지는 쓸 수 없습니다"


def test_files_without_markdown():
    files = [("files", ("a.png", _png(), "image/png"))]
    assert _error(client.post("/convert", data={"source": "files"}, files=files)) == "변환할 .md 파일이 없습니다: /"


def test_zip_of_a_folder_with_subfolder_images(tmp_path):
    archive = _zip({
        "docs/2.md": b"# Two\n",
        "docs/10.md": b"# Ten\n\n![](img/a.png)\n",
        "docs/img/a.png": _png(),
    })
    response = client.post("/convert", data={"source": "zip"}, files={"archive": ("docs.zip", archive)})

    # The zip holds one folder: its .md files are joined in name order, as the command line does.
    assert _filename(response) == "docs.hwpx"
    doc = _hwpx(response, tmp_path)
    assert [p.text for p in doc.paragraphs if p.text] == ["Two", "Ten"]
    assert len(doc.media.picture_references()) == 1


def test_zip_folder_named_like_markdown_is_skipped(tmp_path):
    archive = _zip({"docs/a.md": b"# A\n", "docs/b.md": b"# B\n", "docs/old.md/note.txt": b"x"})
    response = client.post("/convert", data={"source": "zip"}, files={"archive": ("docs.zip", archive)})

    # Only files are joined, as the command line does: the folder "old.md" is not read as Markdown.
    assert [p.text for p in _hwpx(response, tmp_path).paragraphs if p.text] == ["A", "B"]


def test_zip_path_picks_a_file(tmp_path):
    archive = _zip({"a.md": b"# A\n", "sub/b.md": b"# B\n"})
    response = client.post(
        "/convert", data={"source": "zip", "path": "sub/b.md"}, files={"archive": ("x.zip", archive)}
    )
    assert _filename(response) == "b.hwpx"
    assert [p.text for p in _hwpx(response, tmp_path).paragraphs if p.text] == ["B"]


def test_zip_path_outside_is_refused():
    archive = _zip({"a.md": b"# A\n"})
    response = client.post("/convert", data={"source": "zip", "path": "../"}, files={"archive": ("x.zip", archive)})
    assert _error(response) == "변환할 경로가 없습니다: .."


def test_zip_slip_is_refused():
    archive = _zip({"../evil.md": b"# x\n"})
    detail = _error(client.post("/convert", data={"source": "zip"}, files={"archive": ("x.zip", archive)}))
    assert detail == "zip 안의 경로가 올바르지 않습니다: ../evil.md"


def test_zip_with_cp949_names(tmp_path):
    # A zip from Korean Windows: cp949 names, no UTF-8 flag.
    class Cp949Info(zipfile.ZipInfo):
        def _encodeFilenameFlags(self):
            return self.filename.encode("cp949"), self.flag_bits & ~0x800

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in {"문서.md": "![](그림.png)\n".encode(), "그림.png": _png()}.items():
            zf.writestr(Cp949Info(name), data)
    response = client.post("/convert", data={"source": "zip"}, files={"archive": ("x.zip", buffer.getvalue())})
    assert _filename(response) == "문서.hwpx"
    assert len(_hwpx(response, tmp_path).media.picture_references()) == 1


def test_not_a_zip():
    response = client.post("/convert", data={"source": "zip"}, files={"archive": ("x.zip", b"not a zip")})
    assert _error(response).startswith("zip 파일을 열 수 없습니다")


@pytest.mark.parametrize("repo", ["file:///etc", "git@github.com:a/b.git", "/srv/repo", "ftp://example.com/a.git"])
def test_git_needs_http(repo):
    assert _error(client.post("/convert", data={"source": "git", "repo": repo})) == (
        "git 저장소 주소는 http:// 또는 https:// 로 시작해야 합니다."
    )


def test_git_clone_over_http(tmp_path, monkeypatch):
    # A repository on a closed network may have only http.
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        (Path(command[-1]) / "guide.md").write_text("# 안내\n", encoding="utf-8")
        return web.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    response = client.post("/convert", data={"source": "git", "repo": "http://git.internal/docs/manual.git"})

    assert [p.text for p in _hwpx(response, tmp_path).paragraphs if p.text] == ["안내"]
    assert commands[0][-2] == "http://git.internal/docs/manual.git"
    assert "protocol.http.allow=always" in commands[0] and "protocol.allow=never" in commands[0]


def test_git_ref_must_not_be_an_option():
    response = client.post("/convert", data={"source": "git", "repo": "https://example.com/a.git", "ref": "--upload-pack=x"})
    assert _error(response) == "브랜치·태그 이름이 올바르지 않습니다: --upload-pack=x"


def test_git_clone(tmp_path, monkeypatch):
    # No network in tests: stand in for git, checking the command it is given.
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        root = Path(command[-1])
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("# 안내\n", encoding="utf-8")
        (root / ".git").mkdir()
        return web.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    response = client.post(
        "/convert",
        data={"source": "git", "repo": "https://example.com/user/manual.git", "ref": "v1.0", "path": "docs"},
    )

    assert _filename(response) == "docs.hwpx"  # a folder given as the path names the HWPX
    assert [p.text for p in _hwpx(response, tmp_path).paragraphs if p.text] == ["안내"]
    command = commands[0]
    assert command[command.index("--branch") + 1] == "v1.0"
    assert command[-3:-1] == ["--", "https://example.com/user/manual.git"]
    assert "protocol.allow=never" in command and "core.symlinks=false" in command


def test_symlinks_are_removed(tmp_path):
    (tmp_path / "doc.md").write_text("x", encoding="utf-8")
    os.symlink("/etc/hostname", tmp_path / "link.png")
    os.symlink("/etc", tmp_path / "folder")

    web._remove_symlinks(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.md"]


def test_uploaded_template(tmp_path):
    files = [("files", ("doc.md", b"# A\n", "text/markdown")), ("template", ("t.hwpx", TEMPLATE.read_bytes()))]
    response = client.post("/convert", data={"source": "files"}, files=files)
    assert [p.text for p in _hwpx(response, tmp_path).paragraphs if p.text] == ["A"]


def test_uploaded_template_that_is_not_hwpx():
    files = [("files", ("doc.md", b"# A\n", "text/markdown")), ("template", ("t.hwpx", b"not a template"))]
    assert _error(client.post("/convert", data={"source": "files"}, files=files))


def test_upload_size_limit(monkeypatch):
    monkeypatch.setattr(web, "MAX_UPLOAD", 10)
    files = [("files", ("doc.md", b"# 0123456789\n", "text/markdown"))]
    assert _error(client.post("/convert", data={"source": "files"}, files=files), 413).startswith("올린 파일이 너무 큽니다")


def test_unknown_source():
    assert _error(client.post("/convert", data={"source": "ftp"})) == "알 수 없는 입력 방식입니다: ftp"
