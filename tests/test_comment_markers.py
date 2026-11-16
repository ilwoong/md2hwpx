"""Comment markers, "<!-- @hwpx 명령 인자 -->": what the marker lines and attributes do, hidden from Markdown viewers."""

import re
import zipfile

import pytest

from helpers import _png
from md2hwpx.cli import main
from md2hwpx.convert import DEFAULT_TEMPLATE as TEMPLATE
from md2hwpx.convert import convert
from md2hwpx.source import marker_problems, unknown_styles

DOCUMENT = "# 장\n\n앞 문단\n\n{}\n\n뒤 문단\n\n[표] 표 제목\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## 절\n\n![그림](a.png)\n"


def _section(path) -> str:
    """The section XML of the HWPX *path*, without what is new each conversion: ids and bookmark numbers."""
    xml = zipfile.ZipFile(path).read("Contents/section0.xml").decode()
    xml = re.sub(r'\b(id|instId|instid|fieldid|zOrder|zorder|beginIDRef)="[^"]*"', r'\1=""', xml)
    return re.sub(r"#\d+", "#", xml)


def _converted(tmp_path, name: str, text: str) -> str:
    (tmp_path / "a.png").write_bytes(_png(40, 20))
    md = tmp_path / f"{name}.md"
    md.write_text(text, encoding="utf-8")
    out = tmp_path / f"{name}.hwpx"
    convert(md, TEMPLATE, out)
    return _section(out)


@pytest.mark.parametrize("old, new", [
    ("\\newpage", "<!-- @hwpx 쪽나눔 -->"),
    ("[감추기]", "<!-- @hwpx 감추기 -->"),
    ("[새 번호 쪽 표]", "<!-- @hwpx 새번호 쪽 표 -->"),
    ("[빈 줄 3]", "<!-- @hwpx 빈줄 3 -->"),
    ("[빈 줄]", "<!-- @hwpx 빈줄 -->"),
    ("[여백면] 이 면은 여백", '<!-- @hwpx 여백면 "이 면은 여백" -->'),
    ("[여백면] 여백", "<!-- @hwpx 여백면 여백 -->"),
    ("[여백면]", "<!-- @hwpx 여백면 -->"),
    ("[TOC]", "<!-- @hwpx 차례 -->"),
    ("[TOC 그림 표 링크없음]", "<!-- @hwpx 차례 그림 표 링크없음 -->"),
    ('[TOC 개요 스타일="표그림 제목;본문"]', '<!-- @hwpx 차례 개요 스타일="표그림 제목;본문" -->'),
    # Over lines, with spaces anywhere a space may be.
    ("[새 번호 표 그림]", "<!--\n  @hwpx   새번호\n  표  그림\n-->"),
])
def test_block_marker_does_what_its_marker_line_does(tmp_path, old, new):
    assert _converted(tmp_path, "new", DOCUMENT.format(new)) == _converted(tmp_path, "old", DOCUMENT.format(old))


@pytest.mark.parametrize("old, new", [
    # A marker comment ends the paragraph it is in, as a marker line does.
    ("앞\n\\newpage\n뒤", "앞\n<!-- @hwpx 쪽나눔 -->\n뒤"),
    # In a quote and a list item, it is laid out with their blocks.
    ("> 인용\n>\n> [빈 줄 2]\n> 끝", "> 인용\n>\n> <!-- @hwpx 빈줄 2 -->\n> 끝"),
    ("- 항목\n\n  [빈 줄 2]\n- 다음", "- 항목\n\n  <!-- @hwpx 빈줄 2 -->\n- 다음"),
])
def test_block_marker_placement(tmp_path, old, new):
    assert _converted(tmp_path, "new", new) == _converted(tmp_path, "old", old)


@pytest.mark.parametrize("old, new", [
    ("[표] 제목\n<!-- [cols: 30%, 70%] -->\n| a | b |\n|---|---|\n| 1 | 2 |\n",
     "[표] 제목\n<!-- @hwpx 열너비 30% 70% -->\n| a | b |\n|---|---|\n| 1 | 2 |\n"),
    ("![그림](a.png){width=50% height=30mm}\n", "<!-- @hwpx 그림크기 너비=50% 높이=30mm -->\n![그림](a.png)\n"),
    # Between a caption line and its picture, as a [cols: ...] comment may be.
    ("[그림] 제목\n\n![](a.png){width=50%}\n", "[그림] 제목\n\n<!-- @hwpx 그림크기 너비=50% -->\n\n![](a.png)\n"),
    # The first picture of the next block; what the picture's own {...} says wins.
    ("앞 ![](a.png){width=50% height=30mm} ![](a.png)\n",
     "<!-- @hwpx 그림크기 너비=30% 높이=30mm -->\n앞 ![](a.png){width=50%} ![](a.png)\n"),
])
def test_setting_marker_does_what_its_comment_or_attributes_do(tmp_path, old, new):
    assert _converted(tmp_path, "new", new) == _converted(tmp_path, "old", old)


@pytest.mark.parametrize("old, new", [
    ('::: {custom-style="표그림 제목"}\n가\n\n> 나\n\n:::\n\n다\n',
     '<!-- @hwpx 스타일 "표그림 제목" -->\n가\n\n> 나\n<!-- @hwpx 스타일끝 -->\n\n다\n'),
    # Inside another style, and in a list item; a name without spaces needs no quotes.
    (':::: {custom-style="바탕글"}\n가\n::: {custom-style="표그림 제목"}\n나\n:::\n다\n::::\n',
     '<!-- @hwpx 스타일 바탕글 -->\n가\n<!-- @hwpx 스타일 "표그림 제목" -->\n나\n<!-- @hwpx 스타일끝 -->\n'
     '다\n<!-- @hwpx 스타일끝 -->\n'),
    ('- 항목\n\n  ::: {custom-style="바탕글"}\n  가\n  :::\n- 다음\n',
     "- 항목\n\n  <!-- @hwpx 스타일 바탕글 -->\n  가\n  <!-- @hwpx 스타일끝 -->\n- 다음\n"),
])
def test_style_markers_do_what_a_custom_style_div_does(tmp_path, old, new):
    assert _converted(tmp_path, "new", new) == _converted(tmp_path, "old", old)


def test_marker_problems_are_left_out(tmp_path):
    # Each of these is dropped, writing nothing, as an ordinary comment is.
    text = (
        "가\n\n<!-- @hwpx 쪽나누기 -->\n\n<!-- @hwpx -->\n\n<!-- @hwpx 감추기 쪽 -->\n\n<!-- @hwpx 빈줄 11 -->\n\n"
        "<!-- @hwpx 새번호 -->\n\n<!-- @hwpx 차례 목차 -->\n\n<!-- @hwpx 여백면 이 면은 여백 -->\n\n"
        '<!-- @hwpx 여백면 "문구 -->\n\n<!-- @hwpx 그림크기 폭=50% -->\n\n![](a.png)\n\n'
        "<!-- @hwpx 그림크기 너비=50px -->\n\n![](a.png)\n\n<!-- @hwpx 그림크기 너비=50% -->\n\n나\n\n"
        "<!-- @hwpx 스타일 바탕글 -->\n\n- 항목\n\n  <!-- @hwpx 스타일끝 -->\n\n"
        "다 <!-- @hwpx 쪽나눔 --> 라\n\n<!-- @hwpx 쪽나눔 --> 마\n"
    )
    # An ordinary comment keeps the list item as it is (loose).
    plain = "가\n\n![](a.png)\n\n![](a.png)\n\n나\n\n- 항목\n\n  <!-- 주석 -->\n\n다 라\n"
    assert _converted(tmp_path, "new", text) == _converted(tmp_path, "old", plain)


def test_marker_problems_say_where_and_why():
    text = (
        "---\ntitle: x\n---\n가\n\n<!-- @hwpx 쪽나누기 -->\n\n<!-- @hwpx -->\n\n<!-- @hwpx 빈줄 11 -->\n\n"
        "<!-- @hwpx 그림크기 너비=50% -->\n\n나\n\n<!-- @hwpx 스타일 바탕글 -->\n\n- 항목\n\n"
        "  <!-- @hwpx 스타일끝 -->\n\n| a |\n|---|\n| 다 <!-- @hwpx 감추기 --> |\n\n라\n여러 줄 <!--\n-->\n"
        "마 <!-- @hwpx 쪽나눔 -->\n\n<!-- @hwpx 쪽나눔 --> 바\n\n`<!-- @hwpx 쪽나눔 -->`\n\n"
        "```\n<!-- @hwpx 쪽나눔 -->\n```\n"
    )
    assert marker_problems(text) == [
        (6, "@hwpx 쪽나누기", "모르는 명령이라"),
        (8, "@hwpx", "명령이 없어"),
        (10, "@hwpx 빈줄 11", "인자가 맞지 않아"),
        (12, "@hwpx 그림크기 너비=50%", "바로 뒤에 그림이 없어"),
        (16, "@hwpx 스타일 바탕글", "짝이 되는 스타일끝이 없어"),
        (20, "@hwpx 스타일끝", "짝이 되는 스타일이 없어"),
        (24, "@hwpx 감추기", "줄에 따로 있지 않아"),
        (29, "@hwpx 쪽나눔", "줄에 따로 있지 않아"),
        (31, "@hwpx 쪽나눔", "줄에 따로 있지 않아"),
    ]


def test_comments_nearly_markers_are_warned_about(tmp_path):
    text = (
        "가\n\n<!-- @HWPX 쪽나눔 -->\n\n<!-- @hwpx: 쪽나눔 -->\n\n<!-- @hwp 쪽나눔 -->\n\n<!-- @ hwpx 쪽나눔 -->\n\n"
        "<!-- @hwpx쪽나눔 -->\n\n<!-- @HWPX\n감추기 -->\n\n"
        # Other comments, and code: no warning.
        "<!-- @todo 고치기 -->\n\n<!-- @hwpdoc -->\n\n<!-- hwpx 쪽나눔 -->\n\n<!-- 메모 @hwp -->\n\n"
        "```\n<!-- @HWPX 쪽나눔 -->\n```\n\n    <!-- @HWPX 쪽나눔 -->\n"
    )
    form = "'@hwpx 명령' 꼴이 아니어서"
    assert marker_problems(text) == [
        (3, "@HWPX 쪽나눔", form),
        (5, "@hwpx: 쪽나눔", form),
        (7, "@hwp 쪽나눔", form),
        (9, "@ hwpx 쪽나눔", form),
        (11, "@hwpx쪽나눔", form),
        (13, "@HWPX 감추기", form),
    ]
    # They are dropped, as before.
    assert _converted(tmp_path, "new", "가\n\n<!-- @HWPX 쪽나눔 -->\n\n나\n") == _converted(tmp_path, "old", "가\n\n나\n")


def test_unknown_styles_of_markers():
    text = '가\n\n<!-- @hwpx 스타일 "없는 스타일" -->\n나\n<!-- @hwpx 스타일끝 -->\n\n<!-- @hwpx 차례 스타일="없음;본문" -->\n'
    assert unknown_styles(text, {"본문"}) == [
        (3, "없는 스타일", "본문 스타일로 넣었습니다"),
        (7, "없음", "차례에 모을 수 없습니다"),
    ]


def test_command_line_warns_about_markers(tmp_path, capsys):
    md = tmp_path / "doc.md"
    md.write_text("가\n\n<!-- @hwpx 쪽나누기 -->\n\n<!-- @hwpx 열너비 30% -->\n| a | b |\n|---|---|\n", encoding="utf-8")

    assert main([str(md), "-o", str(tmp_path / "doc.hwpx")]) == 0

    assert capsys.readouterr().err.splitlines() == [
        f"md2hwpx: 경고: {md}:3: 주석 마커(@hwpx 쪽나누기)는 모르는 명령이라 무시했습니다",
        f"md2hwpx: 경고: {md}:5: 열 너비 지정은 값 1개가 표의 열 2개와 달라 무시했습니다",
    ]
