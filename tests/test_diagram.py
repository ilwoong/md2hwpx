import shutil

import pytest

from md2hwpx.diagram import (
    DiagramError,
    find_diagram_blocks,
    render_diagrams,
    render_mermaid,
    render_plantuml,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
needs_mmdc = pytest.mark.skipif(shutil.which("mmdc") is None, reason="mmdc not installed")
needs_plantuml = pytest.mark.skipif(shutil.which("plantuml") is None, reason="plantuml not installed")

MERMAID = "graph TD\n  A --> B\n"
PLANTUML = "@startuml\nA -> B: hi\n@enduml\n"


def test_find_backtick_and_tilde_blocks():
    md = f"# T\n```mermaid\n{MERMAID}```\ntext\n~~~plantuml\n{PLANTUML}~~~\n"
    blocks = find_diagram_blocks(md)
    assert [(b.lang, b.code, b.start, b.end) for b in blocks] == [
        ("mermaid", MERMAID, 1, 4),
        ("plantuml", PLANTUML, 6, 10),
    ]


def test_find_info_string_case_and_extra_words():
    blocks = find_diagram_blocks("```Mermaid title=x\ngraph TD\n```\n")
    assert [b.lang for b in blocks] == ["mermaid"]


def test_find_indented_and_long_fence():
    md = "   ````mermaid\ngraph TD\n```\nstill code\n````\n"
    blocks = find_diagram_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].code == "graph TD\n```\nstill code\n"


def test_ignores_four_space_indent_unclosed_and_other_langs():
    md = "    ```mermaid\n    x\n    ```\n```python\n```mermaid\n```\n```plantuml\nnever closed\n"
    assert find_diagram_blocks(md) == []


def test_render_diagrams_without_blocks_returns_input(tmp_path):
    md = "hello\n```python\nprint(1)\n```\n"
    assert render_diagrams(md, tmp_path / "img") == md
    assert not (tmp_path / "img").exists()


def test_missing_tool_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(DiagramError, match="not found"):
        render_mermaid(MERMAID, tmp_path / "a.png")


@needs_mmdc
def test_render_mermaid(tmp_path):
    out = tmp_path / "m.png"
    render_mermaid(MERMAID, out)
    assert out.read_bytes().startswith(PNG_SIGNATURE)


@needs_mmdc
def test_render_mermaid_syntax_error(tmp_path):
    out = tmp_path / "m.png"
    with pytest.raises(DiagramError):
        render_mermaid("graph TD\n  A -->\n", out)
    assert not out.exists()


@needs_plantuml
def test_render_plantuml(tmp_path):
    out = tmp_path / "p.png"
    render_plantuml(PLANTUML, out)
    assert out.read_bytes().startswith(PNG_SIGNATURE)


@needs_plantuml
def test_render_plantuml_syntax_error(tmp_path):
    out = tmp_path / "p.png"
    with pytest.raises(DiagramError):
        render_plantuml("@startuml\nA -> : \n???\n@enduml\n", out)
    assert not out.exists()


@needs_mmdc
@needs_plantuml
def test_render_diagrams_replaces_blocks(tmp_path):
    md = f"# T\n```mermaid\n{MERMAID}```\nmid\n```plantuml\n{PLANTUML}```\nend"
    out_dir = tmp_path / "doc" / "images"
    result = render_diagrams(md, out_dir, link_base=tmp_path / "doc")
    lines = result.split("\n")
    assert lines[0] == "# T" and lines[2] == "mid" and lines[4] == "end"
    assert lines[1].startswith("![](images/mermaid-") and lines[1].endswith(".png)")
    assert lines[3].startswith("![](images/plantuml-") and lines[3].endswith(".png)")
    assert len(list(out_dir.glob("*.png"))) == 2


def test_find_blocks_in_list_items_and_quotes():
    md = f"- 항목\n\n  ```mermaid\n  graph TD\n  ```\n\n> ```plantuml\n> {PLANTUML.splitlines()[0]}\n> ```\n\n- ```mermaid\n  never closed\n"
    blocks = find_diagram_blocks(md)
    assert [(b.lang, b.code, b.start, b.end, b.prefix) for b in blocks] == [
        ("mermaid", "graph TD\n", 2, 4, "  "),
        ("plantuml", "@startuml\n", 6, 8, "> "),
    ]


def test_render_diagrams_keeps_list_and_quote_prefix(tmp_path, monkeypatch):
    from md2hwpx import diagram

    monkeypatch.setitem(diagram._RENDERERS, "mermaid", lambda code, out: out.write_bytes(PNG_SIGNATURE))
    md = "1. 단계\n\n   ```mermaid\n   graph TD\n   ```\n2. 다음\n\n> ```mermaid\n> graph LR\n> ```\n"

    result = render_diagrams(md, tmp_path)

    lines = result.split("\n")
    assert lines[0] == "1. 단계" and lines[3] == "2. 다음"
    assert lines[2].startswith("   ![](mermaid-") and lines[5].startswith("> ![](mermaid-")
