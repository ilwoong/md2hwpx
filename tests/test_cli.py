import shutil

import pytest
from hwpx import HwpxDocument

from helpers import _text
from md2hwpx.cli import main
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE

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


def test_cli_without_template_uses_the_bundled_one(tmp_path, monkeypatch):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # no template.hwpx here

    assert main([str(md)]) == 0
    assert [p.text for p in HwpxDocument.open(tmp_path / "doc.hwpx").paragraphs[1:]] == ["본문"]


def test_cli_without_template_prefers_the_current_folders(tmp_path, monkeypatch, capsys):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    (tmp_path / "template.hwpx").write_bytes(b"not a template")
    monkeypatch.chdir(tmp_path)

    # The folder's template.hwpx is the one opened: it is not HWPX, so the conversion fails on it.
    assert main([str(md)]) == 1
    assert capsys.readouterr().err.startswith("md2hwpx: ")
    assert not (tmp_path / "doc.hwpx").exists()


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


def test_cli_output_new_folder(tmp_path, capsys):
    md = tmp_path / "doc.md"
    md.write_text("본문\n", encoding="utf-8")
    out_dir = tmp_path / "new" / "out"

    # Ending in "/", the output is a folder, made with its parents: not a file named "out".
    assert main([str(md), "-o", f"{out_dir}/", "-t", str(TEMPLATE)]) == 0
    assert (out_dir / "doc.hwpx").is_file()
    assert capsys.readouterr().out.strip() == str(out_dir / "doc.hwpx")


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



def test_cli_leaves_out_drafts(tmp_path, capsys):
    folder = tmp_path / "chapters"
    folder.mkdir()
    (folder / "1.md").write_text("---\ndraft: false\n---\n# 하나\n", encoding="utf-8")
    (folder / "2.md").write_text("---\ntitle: 둘\ndraft: true\n---\n# 둘\n", encoding="utf-8")
    (folder / "3.md").write_text("# 셋\n\ndraft: true\n", encoding="utf-8")  # not front matter: body text
    out = tmp_path / "out.hwpx"

    assert main([str(folder), "-o", str(out), "-t", str(TEMPLATE)]) == 0
    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:] if p.text] == ["하나", "셋", "draft: true"]

    assert main([str(folder), "-o", str(out), "-t", str(TEMPLATE), "--include-draft"]) == 0
    texts = [p.text for p in HwpxDocument.open(tmp_path / "out-1.hwpx").paragraphs[1:] if p.text]
    assert texts == ["하나", "둘", "셋", "draft: true"]


def test_cli_draft_file_needs_include_draft(tmp_path, capsys):
    md = tmp_path / "doc.md"
    md.write_text("---\ndraft: True\n---\n# 초안\n", encoding="utf-8")
    out = tmp_path / "doc.hwpx"

    assert main([str(md), "-o", str(out), "-t", str(TEMPLATE)]) == 1
    assert "모두 draft 입니다 (포함하려면 --include-draft)" in capsys.readouterr().err
    assert not out.exists()

    assert main([str(md), "-o", str(out), "-t", str(TEMPLATE), "--include-draft"]) == 0
    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:] if p.text] == ["초안"]


def test_cli_include_patterns(tmp_path, capsys):
    folder = tmp_path / "book"
    folder.mkdir()
    for name in ("ch_1.md", "ch_2.md", "ch_2b.md", "ch_10.md", "appendix.md", "CH_2c.md"):
        (folder / name).write_text(f"# {name}\n", encoding="utf-8")
    (folder / "ch_2d.md").write_text(f"---\ndraft: true\n---\n# ch_2d.md\n", encoding="utf-8")
    out = tmp_path / "out.hwpx"

    args = [str(folder), "-o", str(out), "-t", str(TEMPLATE)]
    assert main([*args, "--include", "ch_2*.md", "--include", "app*"]) == 0
    # Name order, case kept, drafts still left out.
    assert [p.text for p in HwpxDocument.open(out).paragraphs[1:] if p.text] == ["appendix.md", "ch_2.md", "ch_2b.md"]

    assert main([*args, "--include", "ch_3*.md"]) == 1
    assert "'ch_3*.md' 에 맞는 .md 파일이 없습니다" in capsys.readouterr().err
    assert not (tmp_path / "out-1.hwpx").exists()


def test_cli_chapter_start(tmp_path, capsys):
    md = tmp_path / "ch_2.md"
    md.write_text("# 둘째 장\n\n[표] 표\n| a |\n|---|\n| 1 |\n", encoding="utf-8")
    out = tmp_path / "out.hwpx"

    assert main([str(md), "-o", str(out), "-t", str(TEMPLATE), "--chapter-start", "2"]) == 0
    assert [_text(t.caption.element) for t in HwpxDocument.open(out).tables.all] == ["[표 2-1] 표"]

    for bad in ("0", "x"):
        with pytest.raises(SystemExit):
            main([str(md), "--chapter-start", bad])
    assert "1 이상의 정수여야 합니다" in capsys.readouterr().err


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
    assert main([str(tmp_path / "empty"), "-t", str(TEMPLATE)]) == 1
    err = capsys.readouterr().err
    assert err.count("md2hwpx: ") == 3 and "no .md files" in err
    assert not (tmp_path / "doc.hwpx").exists()


def test_cli_folder_input_with_different_caption_numbering(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "1.md").write_text("---\ncaption-numbering: document\n---\n\n# 장\n", encoding="utf-8")
    (docs / "2.md").write_text("---\ncaption-numbering: chapter\n---\n\n# 장\n", encoding="utf-8")
    out = tmp_path / "out.hwpx"

    assert main([str(docs), "-o", str(out), "-t", str(TEMPLATE)]) == 1

    assert "caption-numbering 가 파일마다 다릅니다" in capsys.readouterr().err
    assert not out.exists()


@needs_mmdc
def test_cli_renders_diagrams(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("```mermaid\ngraph TD\n  A --> B\n```\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    doc = HwpxDocument.open(tmp_path / "doc.hwpx")
    assert len(doc.media.picture_references()) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.hwpx", "doc.md"]  # no images left behind


def test_cli_warns_about_missing_images_with_location(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "book"
    folder.mkdir()
    (folder / "1.md").write_text("# 하나\n\n```\n![](in-code.png)\n```\n", encoding="utf-8")
    (folder / "2.md").write_text("---\nk: v\n---\n![](a.png)\n\n![](b.png)\n", encoding="utf-8")

    assert main([str(folder), "-t", str(TEMPLATE)]) == 0

    # The HWPX is made, with a placeholder for each missing image, and each one is warned about.
    out = capsys.readouterr()
    assert out.err.splitlines() == [
        f"md2hwpx: 경고: {folder / '2.md'}:4: 이미지 파일이 없어 대체 이미지를 넣었습니다: {folder / 'a.png'}",
        f"md2hwpx: 경고: {folder / '2.md'}:6: 이미지 파일이 없어 대체 이미지를 넣었습니다: {folder / 'b.png'}",
    ]
    assert out.out == "book.hwpx\n"
    assert len(HwpxDocument.open(tmp_path / "book.hwpx").media.picture_references()) == 2


def test_cli_warns_about_unknown_styles(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text('![](gone.png)\n\n::: {custom-style="없는 스타일"}\n글\n:::\n', encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    # In line order with the image warnings; the HWPX is made, the paragraph in 본문.
    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: 경고: {md}:1: 이미지 파일이 없어 대체 이미지를 넣었습니다: {tmp_path / 'gone.png'}",
        f"md2hwpx: 경고: {md}:3: 템플릿에 '없는 스타일' 문단 스타일이 없어 본문 스타일로 넣었습니다",
    ]
    assert (tmp_path / "doc.hwpx").is_file()


def test_cli_warns_about_column_widths_it_ignores(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text(
        "---\nk: v\n---\n"
        "<!-- [cols: 30%, 70%] -->\n| a | b |\n|---|---|\n\n"  # fine
        "<!-- [cols: 50%] -->\n| a | b |\n|---|---|\n\n"
        "<!-- [cols: 50, 50] -->\n| a | b |\n|---|---|\n\n"
        "- 항목\n\n  <!--\n  [cols: 0%, 100%]\n  -->\n  | a | b |\n  |---|---|\n\n"
        "<!-- [cols: 50%, 50%] -->\n\n문단\n\n"
        "```\n<!-- [cols: 1%] -->\n```\n",  # in code: text
        encoding="utf-8",
    )

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    # Line numbers count the front matter; the HWPX is made, those tables at their automatic widths.
    ignored = "md2hwpx: 경고: {}:{}: 열 너비 지정은 {} 무시했습니다"
    assert capsys.readouterr().err.splitlines() == [
        ignored.format(md, 8, "값 1개가 표의 열 2개와 달라"),
        ignored.format(md, 12, "값이 모두 0 보다 큰 % 가 아니어서"),
        ignored.format(md, 18, "값이 모두 0 보다 큰 % 가 아니어서"),
        ignored.format(md, 24, "바로 뒤에 표가 없어"),
    ]
    assert (tmp_path / "doc.hwpx").is_file()


def test_cli_warns_about_inline_and_reference_images(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# 제목\n\n앞 ![](inline.png) 뒤\n\n![참조][pic]\n\n[pic]: ref%20image.png\n", encoding="utf-8")

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: 경고: {md}:3: 이미지 파일이 없어 대체 이미지를 넣었습니다: {tmp_path / 'inline.png'}",
        f"md2hwpx: 경고: {md}:5: 이미지 파일이 없어 대체 이미지를 넣었습니다: {tmp_path / 'ref image.png'}",
    ]


def test_cli_warns_about_unresolved_references(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "doc"
    folder.mkdir()
    # A name from another file of the folder is found.
    (folder / "1.md").write_text("[표 #같음] 하나\n| a |\n|---|\n\n[표 #결과] 결과\n| b |\n|---|\n", encoding="utf-8")
    (folder / "2.md").write_text("[@결과] 참고\n\n[표 #같음] 둘\n| c |\n|---|\n\n[@같음] 과\n[@없음]\n", encoding="utf-8")

    assert main([str(folder)]) == 0

    md = folder / "2.md"
    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: 경고: {md}:7: '#같음' 이름의 표·그림이 여럿이라 [@같음] 를 그대로 두었습니다",
        f"md2hwpx: 경고: {md}:8: '#없음' 이름의 표·그림이 없어 [@없음] 를 그대로 두었습니다",
    ]


def test_cli_warns_about_what_footnotes_leave_out(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text(
        "---\nk: v\n---\n"
        "본문[^a] 과[^b]\n\n"
        "[^a]: 문단 [^b] 은 넣음\n\n    - 목록\n\n    ```\n    코드\n    ```\n\n    둘째 문단\n\n"
        "[^b]: 문단만 있는 각주\n",
        encoding="utf-8",
    )

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    # Line numbers count the front matter; the footnotes' paragraphs go in, the rest is left out.
    left_out = "md2hwpx: 경고: {}:{}: 각주 안의 {} 넣지 않았습니다"
    assert capsys.readouterr().err.splitlines() == [
        left_out.format(md, 6, "각주 참조는"),
        left_out.format(md, 8, "목록은"),
        left_out.format(md, 10, "코드블록은"),
    ]
    assert (tmp_path / "doc.hwpx").is_file()


def test_cli_warns_about_lines_that_look_like_markers(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text(
        "---\nk: v\n---\n"
        "[새 번호 쪽 페이지]\n\n[빈 줄 11]\n\n\\newpage 뒤\n\n"
        "[새번호 쪽]\n[toc 목록]\n[감추기 둘]\n\n"
        "> [빈 줄 셋]\n\n"
        # Markers, text that does not start like one, escaped and formatted lines, code and table cells: no warning.
        "\\newpage\n\n[새 번호 체계] 안내\n\n\\[새 번호 체계 안내]\n\n**[빈 줄 11]**\n\n"
        "`[빈 줄 11]`\n\n```\n[빈 줄 11]\n```\n\n| [빈 줄 11] |\n|---|\n",
        encoding="utf-8",
    )

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    # The lines stay text in the HWPX, as before.
    warning = "md2hwpx: 경고: {}:{}: 표기({})는 형식이 맞지 않아 글자로 넣었습니다 (글자 그대로 쓰려면 맨 앞에 \\)"
    assert capsys.readouterr().err.splitlines() == [
        warning.format(md, 4, "[새 번호 쪽 페이지]"),
        warning.format(md, 6, "[빈 줄 11]"),
        warning.format(md, 8, "\\newpage 뒤"),
        warning.format(md, 10, "[새번호 쪽]"),
        warning.format(md, 11, "[toc 목록]"),
        warning.format(md, 12, "[감추기 둘]"),
        warning.format(md, 14, "[빈 줄 셋]"),
    ]
    text = _text(HwpxDocument.open(tmp_path / "doc.hwpx").sections[-1].element)
    assert "[새 번호 쪽 페이지]" in text and "[빈 줄 11]" in text


def test_cli_warns_about_marker_lines_going_away(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text(
        "---\nk: v\n---\n"
        '[TOC 그림 표 링크없음 스타일="요약문-본문"]\n\n\\newpage\n[감추기]\n[새 번호 쪽 표]\n\n'
        "- [빈 줄 3]\n\n> [빈 줄]\n\n[여백면]\n\n[여백면] 이 면은 여백\n\n[여백면] 여백\n\n"
        # pandoc's page break, comment markers, escaped and formatted lines, code, table cells, footnotes: no warning.
        "<!-- @hwpx 감추기 -->\n\n\\[감추기]\n\n**[감추기]**\n\n`[감추기]`\n\n| [감추기] |\n|---|\n\n"
        "본문[^n]\n\n[^n]: [감추기]\n",
        encoding="utf-8",
    )

    assert main([str(md), "-t", str(TEMPLATE)]) == 0

    # They still do what they did.
    warning = "md2hwpx: 경고: {}:{}: 표기({})는 없어질 표기입니다. {} 로 바꾸세요"
    assert capsys.readouterr().err.splitlines() == [
        warning.format(
            md, 4, '[TOC 그림 표 링크없음 스타일="요약문-본문"]', '<!-- @hwpx 차례 그림 표 링크없음 스타일="요약문-본문" -->'
        ),
        warning.format(md, 7, "[감추기]", "<!-- @hwpx 감추기 -->"),
        warning.format(md, 8, "[새 번호 쪽 표]", "<!-- @hwpx 새번호 쪽 표 -->"),
        warning.format(md, 10, "[빈 줄 3]", "<!-- @hwpx 빈줄 3 -->"),
        warning.format(md, 12, "[빈 줄]", "<!-- @hwpx 빈줄 -->"),
        warning.format(md, 14, "[여백면]", "<!-- @hwpx 여백면 -->"),
        warning.format(md, 16, "[여백면] 이 면은 여백", '<!-- @hwpx 여백면 "이 면은 여백" -->'),
        warning.format(md, 18, "[여백면] 여백", "<!-- @hwpx 여백면 여백 -->"),
    ]
    text = _text(HwpxDocument.open(tmp_path / "doc.hwpx").sections[-1].element)
    assert "[새 번호 쪽 표]" not in text and "이 면은 여백" in text
