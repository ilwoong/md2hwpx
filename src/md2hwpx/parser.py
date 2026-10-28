"""The Markdown parser shared by diagram rendering and HWPX conversion."""

import re

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
from mdit_py_plugins.footnote import footnote_plugin

# "{width=50% height="30mm"}": attributes right after an image, as pandoc writes them.
_ATTRIBUTES = re.compile(r"\{([^{}\n]*)\}")
_ATTRIBUTE = re.compile(r'([A-Za-z][\w-]*)=(?:"([^"]*)"|([^\s"]+))')
_IMAGE_ATTRIBUTES = ("width", "height")


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


# CommonMark with GFM tables and strikethrough, footnotes, and image attributes. Raw HTML stays text.
PARSER = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).use(footnote_plugin)
PARSER.inline.ruler.push("image_attributes", _image_attributes)
