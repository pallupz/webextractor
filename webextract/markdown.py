"""HTML/DOM to markdown helpers, shared across extractors.

Text is preserved; non-text content is kept as references: hyperlinks become
[text](href) and images become ![alt](src). Binary media is never downloaded.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser


class _MarkdownText(HTMLParser):
    """Collect visible text as markdown, keeping inline links and images."""

    SKIP = {"script", "style", "head", "noscript", "svg", "nav", "footer"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "article", "section", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []
        self.title: str | None = None
        self._in_title = False
        self._link_buf: list[str] | None = None
        self._link_href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "title":  # lives inside <head> (a SKIP tag); capture anyway
            self._in_title = True
            return
        if tag in self.SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            self._link_href = dict(attrs).get("href")
            self._link_buf = []
        elif tag == "img":
            d = dict(attrs)
            if d.get("src"):
                self.parts.append(f"![{d.get('alt', '')}]({d['src']})")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            return
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._link_buf is not None:
            text = " ".join(self._link_buf).strip()
            href = self._link_href
            self.parts.append(f"[{text}]({href})" if href and text else text)
            self._link_buf = self._link_href = None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
            return
        if self._skip_depth:
            return
        if self._link_buf is not None:
            self._link_buf.append(text)
            return
        self.parts.append(text)

    def text(self) -> str:
        raw = " ".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_markdown(html: str) -> tuple[str | None, str]:
    """Parse an HTML string into (title, markdown_text)."""
    parser = _MarkdownText()
    parser.feed(html)
    title = unescape(parser.title) if parser.title else None
    return title, unescape(parser.text())


# A reusable in-browser serializer: turns a DOM element into markdown text,
# keeping inline links and images. Define it, then call md(someElement).
# Used by JS-based extractors via Selenium's execute_script.
JS_MARKDOWN_FN = r"""
const _BLOCK = new Set(['p','div','li','h1','h2','h3','h4','h5','h6',
                        'blockquote','pre','tr','ul','ol','table']);
function md(root) {
  if (!root) return '';
  let out = '';
  (function walk(node) {
    for (const n of node.childNodes) {
      if (n.nodeType === 3) { out += n.nodeValue; continue; }
      if (n.nodeType !== 1) continue;
      const tag = n.tagName.toLowerCase();
      if (tag === 'a') {
        const href = n.getAttribute('href');
        const txt = n.innerText.trim();
        out += href ? '[' + (txt || href) + '](' + href + ')' : txt;
      } else if (tag === 'img') {
        const src = n.getAttribute('src');
        if (src) out += '![' + (n.getAttribute('alt') || '') + '](' + src + ')';
      } else if (tag === 'br') {
        out += '\n';
      } else {
        const block = _BLOCK.has(tag);
        if (block) out += '\n';
        walk(n);
        if (block) out += '\n';
      }
    }
  })(root);
  return out.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}
"""
