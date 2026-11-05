"""The Markdown parser shared by diagram rendering and HWPX conversion."""

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
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
    HTML block kind 2, so the rest of that line goes too)."""
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
    state.line = line
    return True


def _comment_inline(state: StateInline, silent: bool) -> bool:
    """Drop an HTML comment inside a paragraph, heading or table cell, with the spaces it leaves doubled
    ("앞 <!-- x --> 뒤" → "앞 뒤") or trailing ("제목 <!-- x -->" → "제목")."""
    match = _COMMENT.match(state.src, state.pos)
    if match is None:
        return False
    state.pos = match.end()
    if state.pending.endswith(" "):
        while state.pos < state.posMax and state.src[state.pos] == " ":
            state.pos += 1
        if state.pos == state.posMax and not silent:
            state.pending = state.pending.rstrip(" ")
    return True


def custom_style(info: str) -> str | None:
    """The style a custom-style div's opening line (its "info") names, or None if it names none."""
    match = _CUSTOM_STYLE.fullmatch(info.strip())
    return match[1] if match else None


# CommonMark with GFM tables and strikethrough, footnotes, image attributes and custom-style divs
# ("container_div" nodes; any other ":::" block stays text). HTML comments are dropped; other raw HTML stays text.
PARSER = (
    MarkdownIt("commonmark", {"html": False})
    .enable(["table", "strikethrough"])
    .use(footnote_plugin)
    .use(container_plugin, "div", validate=lambda params, *_: custom_style(params) is not None)
)
PARSER.inline.ruler.push("image_attributes", _image_attributes)
PARSER.block.ruler.before("html_block", "comment_block", _comment_block, {"alt": ["paragraph", "reference", "blockquote"]})
PARSER.inline.ruler.before("html_inline", "comment_inline", _comment_inline)
