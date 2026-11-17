"""md2hwpx-remote.py: what it zips for the web service, and a conversion through the service itself."""

import importlib.util
import io
import urllib.request
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from md2hwpx import web

SCRIPT = web.REMOTE
spec = importlib.util.spec_from_file_location("md2hwpx_remote", SCRIPT)
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)

PNG = (Path(web.__file__).parent / "static" / "missing-image.png").read_bytes()


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)


def test_named_files_leaves_out_code_and_addresses(tmp_path):
    markdown = "\n".join([
        "![a](img/a.png) ![b](<my pic.png> \"제목\") ![c](c%20d.png){width=50%}",
        "![web](https://example.com/x.png) ![abs](/etc/x.png) `![span](span.png)`",
        "```",
        "![code](code.png)",
        "```",
        "![ref][logo]",
        "",
        "[logo]: logo.png",
    ])
    images, definitions = remote.named_files(markdown, tmp_path)
    assert images == [tmp_path / "img/a.png", tmp_path / "my pic.png", tmp_path / "c d.png"]
    assert definitions == [tmp_path / "logo.png"]


def test_collect_a_file_with_images_beside_and_above(tmp_path):
    _write(tmp_path, {
        "docs/guide.md": "![a](img/a.png)\n\n![b](../shared/b.png)\n\n![none](none.png)\n",
        "docs/img/a.png": PNG, "shared/b.png": PNG, "docs/other.md": "# 다른 문서\n", "docs/unused.png": PNG,
    })
    root, files, path = remote.collect(tmp_path / "docs" / "guide.md")
    assert root == tmp_path and path == "docs/guide.md"
    assert [f.relative_to(root).as_posix() for f in files] == ["docs/guide.md", "docs/img/a.png", "shared/b.png"]


def test_collect_a_folder(tmp_path):
    _write(tmp_path, {"b.md": "![x](img/x.png)\n", "a.md": "# A\n", "img/x.png": PNG, "sub/c.md": "# C\n"})
    root, files, path = remote.collect(tmp_path)
    assert root == tmp_path and path == ""
    assert [f.relative_to(root).as_posix() for f in files] == ["a.md", "b.md", "img/x.png"]


def test_output_path_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "guide.md").write_text("# A\n", encoding="utf-8")
    assert remote.output_path(Path("guide.md"), None) == Path("guide.hwpx")
    Path("guide.hwpx").write_bytes(b"")
    assert remote.output_path(Path("guide.md"), None) == Path("guide-1.hwpx")
    assert remote.output_path(Path("guide.md"), "out/") == Path("out/guide.hwpx")


@pytest.fixture
def service(monkeypatch):
    """The script's request goes to the web service's app instead of over the network."""
    client = TestClient(web.app)

    class Answer(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout=None):
        response = client.post("/convert", content=request.data, headers=dict(request.header_items()))
        if response.status_code != 200:
            raise remote.urllib.error.HTTPError(request.full_url, response.status_code, "", {}, io.BytesIO(response.content))
        answer = Answer(response.content)
        answer.headers = response.headers
        return answer

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_converts_through_the_service(tmp_path, monkeypatch, capsys, service):
    _write(tmp_path, {
        "docs/guide.md": "# 제목\n\n![a](img/a.png)\n\n![b](../shared/b.png)\n\n![none](../none.png)\n",
        "docs/img/a.png": PNG, "shared/b.png": PNG,
    })
    monkeypatch.chdir(tmp_path)
    assert remote.main(["docs/guide.md"]) == 0
    out, err = capsys.readouterr()
    assert out == "guide.hwpx\n"
    assert err == "md2hwpx-remote: 경고: docs/guide.md:7: 이미지 파일이 없어 대체 이미지를 넣었습니다: none.png\n"
    with zipfile.ZipFile(tmp_path / "guide.hwpx") as zf:
        assert sum(name.startswith("BinData/") for name in zf.namelist()) == 3


def test_script_download_and_its_tab():
    client = TestClient(web.app)
    response = client.get("/md2hwpx-remote.py")
    assert response.content == SCRIPT.read_bytes()
    assert response.headers["content-disposition"] == 'attachment; filename="md2hwpx-remote.py"'
    page = client.get("/").text
    assert 'value="script"' in page and 'href="md2hwpx-remote.py"' in page


def test_service_error_is_shown(tmp_path, monkeypatch, capsys, service):
    (tmp_path / "guide.md").write_text("# 제목\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert remote.main(["guide.md", "--chapter-start", "0"]) == 1
    assert capsys.readouterr().err == "md2hwpx-remote: 시작 장 번호는 1 이상의 정수여야 합니다: 0\n"
    assert not (tmp_path / "guide.hwpx").exists()
