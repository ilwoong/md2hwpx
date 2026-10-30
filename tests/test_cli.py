import shutil
from pathlib import Path

import pytest
from hwpx import HwpxDocument

from md2hwpx.cli import main
from test_convert import _text

TEMPLATE = Path(__file__).resolve().parent.parent / "template.hwpx"
needs_mmdc = pytest.mark.skipif(shutil.which("mmdc") is None, reason="mmdc not installed")


def test_cli_without_arguments_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: md2hwpx") and "--output" in out and "--template" in out


def test_cli_default_output_in_current_folder(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    src.mkdir()
    md = src / "doc.md"
    md.write_text("# 제목\n\n본문\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    assert capsys.readouterr().out.strip() == "doc.hwpx"
    assert [p.text for p in HwpxDocument.open(tmp_path / "doc.hwpx").paragraphs[1:]] == ["제목", "본문"]
    assert not (src / "doc.hwpx").exists()


def test_cli_output_option(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    out = tmp_path / "out" / "result.hwpx"
    out.parent.mkdir()

    assert main([str(md), "-o", str(out), "--template", str(TEMPLATE)]) == 0
    assert out.is_file()


def test_cli_output_folder(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    assert main([str(md), "-o", str(out_dir), "-t", str(TEMPLATE)]) == 0
    assert (out_dir / "doc.hwpx").is_file()


def test_cli_does_not_overwrite(tmp_path, capsys):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    out = tmp_path / "doc.hwpx"
    out.write_bytes(b"existing")

    assert main([str(md), "-o", str(out), "-t", str(TEMPLATE)]) == 0
    assert main([str(md), "-o", str(out), "-t", str(TEMPLATE)]) == 0
    assert main([str(md), "-o", str(tmp_path), "-t", str(TEMPLATE)]) == 0

    assert capsys.readouterr().out.split() == [str(tmp_path / f"doc-{n}.hwpx") for n in (1, 2, 3)]
    assert out.read_bytes() == b"existing"


def test_cli_folder_input(tmp_path, monkeypatch):
    folder = tmp_path / "chapters"
    folder.mkdir()
    (folder / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"IHDR" + (1).to_bytes(4, "big") * 2)
    (folder / "10-end.md").write_text("---\ntitle: 끝\n---\n# 끝\n", encoding="utf-8")  # every file has front matter
    (folder / "2-body.md").write_text("---\nk: v\n---\n[표] 둘째\n| a |\n|---|\n| 1 |", encoding="utf-8")  # no final newline
    (folder / "1-intro.md").write_text(
        "---\ntitle: 소개\n---\n# 소개\n\n[표] 첫째\n| b |\n|---|\n| 2 |\n\n![](a.png)\n", encoding="utf-8"
    )
    (folder / "notes.txt").write_text("무시\n", encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub" / "0-skip.md").write_text("# 하위 폴더\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main([str(folder), "-t", str(TEMPLATE)]) == 0

    doc = HwpxDocument.open(tmp_path / "chapters.hwpx")
    assert [p.text for p in doc.paragraphs[1:] if p.text] == ["소개", "끝"]
    assert [_text(t.caption.element) for t in doc.tables.all] == ["[표 1-1] 첫째", "[표 1-2] 둘째"]
    assert len(doc.media.picture_references()) == 1


def test_cli_folder_input_named_dot(tmp_path, monkeypatch):
    folder = tmp_path / "book"
    folder.mkdir()
    (folder / "a.md").write_text("본문\n", encoding="utf-8")
    monkeypatch.chdir(folder)

    assert main([".", "-t", str(TEMPLATE)]) == 0
    assert (folder / "book.hwpx").is_file()


def test_cli_reports_missing_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()
    md = tmp_path / "doc.md"
    md.write_text("![](missing.png)\n", encoding="utf-8")

    assert main([str(tmp_path / "nope.md"), "-t", str(TEMPLATE)]) == 1
    assert main([str(md), "-t", str(tmp_path / "nope.hwpx")]) == 1
    assert main([str(md), "-t", str(TEMPLATE)]) == 1
    assert main([str(tmp_path / "empty"), "-t", str(TEMPLATE)]) == 1
    err = capsys.readouterr().err
    assert err.count("md2hwpx: ") == 4 and "missing.png" in err and "no .md files" in err
    assert not (tmp_path / "doc.hwpx").exists()


@needs_mmdc
def test_cli_renders_diagrams(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("```mermaid\ngraph TD\n  A --> B\n```\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    doc = HwpxDocument.open(tmp_path / "doc.hwpx")
    assert len(doc.media.picture_references()) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.hwpx", "doc.md"]  # no images left behind


def test_cli_reports_missing_images_with_location(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "book"
    folder.mkdir()
    (folder / "1.md").write_text("# 하나\n\n```\n![](in-code.png)\n```\n", encoding="utf-8")
    (folder / "2.md").write_text("---\nk: v\n---\n![](a.png)\n\n![](b.png)\n", encoding="utf-8")

    assert main([str(folder), "-t", str(TEMPLATE)]) == 1

    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: {folder / '2.md'}:4: 이미지 파일이 없습니다: {folder / 'a.png'}",
        f"md2hwpx: {folder / '2.md'}:6: 이미지 파일이 없습니다: {folder / 'b.png'}",
    ]
    assert not (tmp_path / "book.hwpx").exists()


def test_cli_reports_inline_and_reference_images(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# 제목\n\n앞 ![](inline.png) 뒤\n\n![참조][pic]\n\n[pic]: ref%20image.png\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 1

    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: {md}:3: 이미지 파일이 없습니다: {tmp_path / 'inline.png'}",
        f"md2hwpx: {md}:5: 이미지 파일이 없습니다: {tmp_path / 'ref image.png'}",
    ]
