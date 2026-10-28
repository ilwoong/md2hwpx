"""The Markdown parser shared by diagram rendering and HWPX conversion."""

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin

# CommonMark with GFM tables and strikethrough, and footnotes. Raw HTML stays text.
PARSER = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).use(footnote_plugin)
