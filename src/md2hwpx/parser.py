"""The Markdown parser shared by diagram rendering and HWPX conversion."""

import itertools
import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.footnote import footnote_plugin

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
    match = _COLUMNS.fullmatch(state.getLines(start, line, state.blkIndent, False).strip())
    if match is not None:
        token = state.push("table_columns", "", 0)
        token.meta["percents"] = _column_percents(match[1])
        token.map = [start, line]
    state.line = line
    return True


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
