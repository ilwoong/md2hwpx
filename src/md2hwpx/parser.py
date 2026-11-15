"""The Markdown parser shared by diagram rendering and HWPX conversion."""

import itertools
import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.footnote import footnote_plugin

from md2hwpx.pieces import match_marker

# "{width=50% height="30mm"}": attributes right after an image, as pandoc writes them.
_ATTRIBUTES = re.compile(r"\{([^{}\n]*)\}")
_ATTRIBUTE = re.compile(r'([A-Za-z][\w-]*)=(?:"([^"]*)"|([^\s"]+))')
_IMAGE_ATTRIBUTES = ("width", "height")
# pandoc's fenced div that names a paragraph style: "::: {custom-style="이름"}" ... ":::".
_CUSTOM_STYLE = re.compile(r'\{\s*custom-style="([^"]*)"\s*\}')
# An HTML comment, as CommonMark reads one: "<!-->", "<!--->" or "<!--" ... "-->".
_COMMENT = re.compile(r"<!---?>|<!--.*?-->", re.DOTALL)
# A comment block that sets the next table's column widths: "<!-- [cols: 30%, 70%] -->".
_COLUMNS = re.compile(r"<!--\s*\[cols:\s*([^\]]*)\]\s*-->")
# Why a "[cols: ...]" comment set no widths (see _table_columns), as a Korean clause.
COLUMN_PROBLEMS = {
    "values": "값이 모두 0 보다 큰 % 가 아니어서",
    "no_table": "바로 뒤에 표가 없어",
    "count": "값 {values}개가 표의 열 {columns}개와 달라",
}
_PERCENT = re.compile(r"\s*(\d+(?:\.\d+)?)\s*%\s*")
# A comment marker, "<!-- @hwpx 명령 인자 ... -->": what a marker line, a "[cols: ...]" comment, image attributes
# or a custom-style div does, hidden from Markdown viewers. Its body is the command and arguments.
_MARKER = re.compile(r"<!--\s*@hwpx(?:\s+((?:(?!-->).)*?))?\s*-->", re.DOTALL)
_MARKER_START = re.compile(r"<!--\s*@hwpx(?=\s|-->)")
# A command or argument: words, and "..." holding spaces ("이 면은 여백", 스타일="A B").
_ARGUMENT = re.compile(r'(?:[^\s"]|"[^"]*")+')
_NAME = re.compile(r'"([^"]+)"|([^\s"]+)')  # a style name or blank page wording: one word, or in quotes
# Commands that do what a marker line does: the marker, and the line they stand for ("{}": the arguments).
_LINE_COMMANDS = {
    "쪽나눔": ("page_break", "\\newpage{}"),
    "감추기": ("hide_page", "[감추기{}]"),
    "새번호": ("restart", "[새 번호{}]"),
    "빈줄": ("blank_lines", "[빈 줄{}]"),
    "차례": ("toc", "[TOC{}]"),
}
_SIZE_KEYS = {"너비": "width", "높이": "height"}  # 그림크기 arguments: the image attributes they set
_SIZE = re.compile(r"(\d+(?:\.\d+)?)(?:%|mm)")
# Why a comment marker is left out, as a Korean clause.
_MARKER_ARGUMENTS = "인자가 맞지 않아"
_MARKER_ALONE = "줄에 따로 있지 않아"


def _image_attributes(state: StateInline, silent: bool) -> bool:
    """Take a "{...}" block right after an image: its width / height go on the image, the rest is dropped."""
    if state.src[state.pos] != "{" or state.pending or not state.tokens or state.tokens[-1].type != "image":
        return False
    match = _ATTRIBUTES.match(state.src, state.pos)
    if match is None:
        return False
    if not silent:
        for key, quoted, bare in _ATTRIBUTE.findall(match[1]):
            if key in _IMAGE_ATTRIBUTES:
                state.tokens[-1].attrSet(key, quoted or bare)
    state.pos = match.end()
    return True


def _comment_block(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    """Drop an HTML comment block: from a line starting with "<!--" to the line holding "-->" (CommonMark's
    HTML block kind 2, so the rest of that line goes too). A "[cols: ...]" one is kept as a "table_columns"
    token, for _table_columns: its percents, or None if they cannot be read."""
    if state.is_code_block(start):
        return False
    pos = state.bMarks[start] + state.tShift[start]
    if not state.src.startswith("<!--", pos):
        return False
    if silent:
        return True
    line = start
    while line < end:
        if line > start and state.sCount[line] < state.blkIndent:
            break
        text = state.src[state.bMarks[line] + state.tShift[line] : state.eMarks[line]]
        line += 1
        if "-->" in text:
            break
    block = state.getLines(start, line, state.blkIndent, False).strip()
    if (match := _COLUMNS.fullmatch(block)) is not None:
        token = state.push("table_columns", "", 0)
        token.meta["percents"] = _column_percents(match[1])
        token.map = [start, line]
    elif (match := _MARKER.fullmatch(block)) is not None:
        _marker_token(state, block, match[1] or "", start, line)
    else:
        # A marker with something else in its block: an ordinary comment, or text after it on its line.
        for comment in _COMMENT.finditer(block):
            if _MARKER_START.match(comment[0]):
                at = start + block[: comment.start()].count("\n")
                _marker_problems(state.env).append((at, _label(comment[0]), _MARKER_ALONE))
    state.line = line
    return True


def _marker_problems(env: dict) -> list[tuple[int, str, str]]:
    """The comment markers left out, as (line index, label, why: a Korean clause), for the warnings (see
    source.marker_problems)."""
    return env.setdefault("marker_problems", [])


def _label(comment: str) -> str:
    """A comment marker as a warning names it: "@hwpx 명령 인자", its spaces as one."""
    return " ".join(comment[4:-3].split())


def _name(argument: str) -> str | None:
    """A style name or blank page wording *argument* without its quotes, or None if it is not one."""
    match = _NAME.fullmatch(argument)
    return match and (match[1] or match[2])


def _read_marker(body: str) -> dict | str:
    """What a comment marker's *body* (its command and arguments) asks for, as its token's meta, or why it
    cannot be read (a Korean clause)."""
    words = _ARGUMENT.findall(body)
    if not words:
        return "명령이 없어"
    command, arguments = words[0], words[1:]
    if command not in _LINE_COMMANDS and command not in ("여백면", "열너비", "그림크기", "스타일", "스타일끝"):
        return "모르는 명령이라"
    if body.count('"') % 2:
        return _MARKER_ARGUMENTS
    if command in _LINE_COMMANDS:
        # Read as the marker line it stands for, so both take the same arguments.
        marker, line = _LINE_COMMANDS[command]
        found = match_marker(line.format("".join(f" {a}" for a in arguments)))
        return {"marker": marker, "words": found[1]} if found and found[0] == marker else _MARKER_ARGUMENTS
    if command == "열너비":
        return {"percents": _column_percents(",".join(arguments))}
    if command == "그림크기":
        size = {}
        for argument in arguments:
            key, _, value = argument.partition("=")
            value = value.strip('"')
            match = _SIZE.fullmatch(value)
            if key not in _SIZE_KEYS or _SIZE_KEYS[key] in size or match is None or float(match[1]) == 0:
                return _MARKER_ARGUMENTS
            size[_SIZE_KEYS[key]] = value
        return {"size": size} if size else _MARKER_ARGUMENTS
    names = [_name(a) for a in arguments]
    if command == "여백면" and len(names) <= 1 and None not in names:
        return {"marker": "blank_page", "words": names[0].strip() if names else ""}
    if command == "스타일" and len(names) == 1 and names[0] is not None:
        return {"style": names[0]}
    if command == "스타일끝" and not names:
        return {"style_end": True}
    return _MARKER_ARGUMENTS


def _marker_token(state: StateBlock, comment: str, body: str, start: int, end: int) -> None:
    """The token of the comment marker *comment* (*body*: its command and arguments) on lines *start* to *end*:
    "table_columns" for 열너비, as a "[cols: ...]" comment gives, else "hwpx_marker" (see _markers). One that
    cannot be read gives none, and a problem."""
    read = _read_marker(body)
    if isinstance(read, str):
        _marker_problems(state.env).append((start, _label(comment), read))
        return
    if "percents" in read:
        token = state.push("table_columns", "", 0)
        token.meta["percents"] = read["percents"]
    else:
        token = state.push("hwpx_marker", "", 0)
        token.meta = {**read, "label": _label(comment)}
    token.map = [start, end]


def _markers(state: StateCore) -> None:
    """Put each comment marker's setting where it goes, once inline content is parsed:

    - 그림크기: its width / height on the first image of the next block, where the image's own "{...}" does not
      set them; the token is dropped.
    - 스타일 ... 스타일끝: a custom-style div ("container_div" tokens) holding the blocks between them, which
      must be in the same block (the document, a list item, a quote).
    - A marker inside a line ("hwpx_misplaced", see _comment_inline) is dropped.

    What cannot be done goes in env "marker_problems" (see _marker_problems). The markers that do what a marker
    line does stay "hwpx_marker" tokens, for the converter.
    """
    problems = _marker_problems(state.env)
    tokens: list[Token | None] = []
    opened: list[tuple[int, int, Token]] = []  # 스타일 not closed yet: its index in tokens, depth inside, marker
    depth = line = 0

    def unclosed() -> None:
        nonlocal depth
        index, _, marker = opened.pop()
        tokens[index] = None
        depth -= 1
        problems.append((marker.map[0], marker.meta["label"], "짝이 되는 스타일끝이 없어"))

    for k, token in enumerate(state.tokens):
        if token.map is not None:
            line = token.map[0]  # a table cell's inline content has no lines of its own: its row's are the nearest
        if token.type == "inline":
            _misplaced_markers(token, line, problems)
        if token.type != "hwpx_marker" or "marker" in token.meta:
            if token.nesting < 0:
                # The block a 스타일 is in ends before its 스타일끝.
                while opened and opened[-1][1] == depth:
                    unclosed()
                depth -= 1
            elif token.nesting > 0:
                depth += 1
            tokens.append(token)
        elif "size" in token.meta:
            image = _next_image(state.tokens, k)
            if image is None:
                problems.append((token.map[0], token.meta["label"], "바로 뒤에 그림이 없어"))
                continue
            for key, value in token.meta["size"].items():
                if image.attrGet(key) is None:
                    image.attrSet(key, value)
        elif "style" in token.meta:
            div = Token("container_div_open", "div", 1, map=token.map, info=f'{{custom-style="{token.meta["style"]}"}}')
            depth += 1
            opened.append((len(tokens), depth, token))
            tokens.append(div)
        elif opened and opened[-1][1] == depth:
            opened.pop()
            depth -= 1
            tokens.append(Token("container_div_close", "div", -1))
        else:
            problems.append((token.map[0], token.meta["label"], "짝이 되는 스타일이 없어"))
    while opened:
        unclosed()
    state.tokens = [token for token in tokens if token is not None]


def _next_image(tokens: list[Token], k: int) -> Token | None:
    """The first image of the paragraph right after *tokens*[*k*], if there is one."""
    if k + 2 >= len(tokens) or tokens[k + 1].type != "paragraph_open":
        return None
    return next((child for child in tokens[k + 2].children or [] if child.type == "image"), None)


def _misplaced_markers(inline: Token, line: int, problems: list) -> None:
    """Drop the comment markers inside the lines of *inline*, starting at *line*, as problems."""
    children = []
    offset = 0
    for child in inline.children or []:
        if child.type in ("softbreak", "hardbreak"):
            offset += 1
        elif child.type == "html_comment":
            offset += child.content.count("\n")
        elif child.type == "hwpx_misplaced":
            problems.append((line + offset, child.content, _MARKER_ALONE))
            continue
        children.append(child)
    inline.children = children


def _column_percents(values: str) -> list[float] | None:
    """The percents of a "[cols: ...]" comment's *values* ("30%, 70%"), or None if one is not a percent above 0."""
    percents = [_PERCENT.fullmatch(part) for part in values.split(",")]
    if not all(percents) or not all(float(p[1]) > 0 for p in percents):
        return None
    return [float(p[1]) for p in percents]


def _table_columns(state: StateCore) -> None:
    """Put a "table_columns" token's percents on the table right after it (meta "column_percents") if there is
    one value for each of its columns, and drop the token: what comes before the comment, a caption line, then
    reads the table as the next block.

    Comments that set no widths go in env "column_problems" as (line index, COLUMN_PROBLEMS key, number of
    values, number of columns), for the warnings (see source.column_problems).
    """
    tokens = []
    for k, token in enumerate(state.tokens):
        if token.type != "table_columns":
            tokens.append(token)
            continue
        percents = token.meta["percents"]
        following = state.tokens[k + 1 :]
        columns = None
        if following and following[0].type == "table_open":
            # The header row's cells: markdown-it gives every row as many.
            header = itertools.takewhile(lambda t: t.type != "tr_close", following)
            columns = sum(t.type == "th_open" for t in header)
        if percents is None:
            problem = "values"
        elif columns is None:
            problem = "no_table"
        elif len(percents) != columns:
            problem = "count"
        else:
            following[0].meta["column_percents"] = percents
            continue
        state.env.setdefault("column_problems", []).append(
            (token.map[0], problem, len(percents or []), columns)
        )
    state.tokens = tokens


def _comment_inline(state: StateInline, silent: bool) -> bool:
    """Drop an HTML comment inside a paragraph, heading or table cell, with the spaces it leaves doubled
    ("앞 <!-- x --> 뒤" → "앞 뒤") or trailing ("제목 <!-- x -->" → "제목").

    One running over lines leaves an "html_comment" token holding it, which writes nothing: the source lines
    it took still count where a line's number is worked out from the soft breaks (see pieces._lines)."""
    match = _COMMENT.match(state.src, state.pos)
    if match is None:
        return False
    state.pos = match.end()
    if state.pending.endswith(" "):
        while state.pos < state.posMax and state.src[state.pos] == " ":
            state.pos += 1
        if state.pos == state.posMax and not silent:
            state.pending = state.pending.rstrip(" ")
    if _MARKER_START.match(match[0]) and not silent:
        # A comment marker belongs on a line of its own: _markers drops it, as a problem.
        state.push("hwpx_misplaced", "", 0).content = _label(match[0])
    if "\n" in match[0] and not silent:
        state.push("html_comment", "", 0).content = match[0]
    return True


def custom_style(info: str) -> str | None:
    """The style a custom-style div's opening line (its "info") names, or None if it names none."""
    match = _CUSTOM_STYLE.fullmatch(info.strip())
    return match[1] if match else None


# CommonMark with GFM tables and strikethrough, footnotes, image attributes and custom-style divs
# ("container_div" nodes; any other ":::" block stays text). HTML comments are dropped, a "[cols: ...]" one
# setting the next table's column widths (its "column_percents" meta); other raw HTML stays text.
PARSER = (
    MarkdownIt("commonmark", {"html": False})
    .enable(["table", "strikethrough"])
    .use(footnote_plugin)
    .use(container_plugin, "div", validate=lambda params, *_: custom_style(params) is not None)
)
PARSER.inline.ruler.push("image_attributes", _image_attributes)
PARSER.block.ruler.before("html_block", "comment_block", _comment_block, {"alt": ["paragraph", "reference", "blockquote"]})
PARSER.inline.ruler.before("html_inline", "comment_inline", _comment_inline)
PARSER.core.ruler.after("block", "table_columns", _table_columns)
PARSER.core.ruler.after("inline", "hwpx_markers", _markers)
