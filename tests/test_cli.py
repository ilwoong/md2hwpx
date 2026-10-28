import shutil
from pathlib import Path

import pytest
from hwpx import HwpxDocument

from md2hwpx.cli import main

TEMPLATE = Path(__file__).resolve().parent.parent / "template.hwpx"
needs_mmdc = pytest.mark.skipif(shutil.which("mmdc") is None, reason="mmdc not installed")


def test_cli_default_output(tmp_path, capsys):
    md = tmp_path / "doc.md"
    md.write_text("# 제목\n\n본문\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    out = tmp_path / "doc.hwpx"
    assert capsys.readouterr().out.strip() == str(out)
    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:]] == ["제목", "본문"]


def test_cli_output_option(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    out = tmp_path / "out" / "result.hwpx"
    out.parent.mkdir()

    assert main([str(md), "-o", str(out), "--template", str(TEMPLATE)]) == 0
    assert out.is_file()


def test_cli_reports_missing_files(tmp_path, capsys):
    md = tmp_path / "doc.md"
    md.write_text("![](missing.png)\n", encoding="utf-8")

    assert main([str(tmp_path / "nope.md"), "-t", str(TEMPLATE)]) == 1
    assert main([str(md), "-t", str(tmp_path / "nope.hwpx")]) == 1
    assert main([str(md), "-t", str(TEMPLATE)]) == 1
    err = capsys.readouterr().err
    assert err.count("md2hwpx: ") == 3 and "missing.png" in err
    assert not (tmp_path / "doc.hwpx").exists()


@needs_mmdc
def test_cli_renders_diagrams(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("```mermaid\ngraph TD\n  A --> B\n```\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    doc = HwpxDocument.open(tmp_path / "doc.hwpx")
    assert len(doc.media.picture_references()) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.hwpx", "doc.md"]  # no images left behind
