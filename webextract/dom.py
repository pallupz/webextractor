"""A small, lenient HTML element tree built on stdlib HTMLParser.

The markdown helpers in `markdown.py` flatten a page to text; this builds a
queryable tree instead, so extractors that need *specific* elements (a price
node, each review block) can find them by id / class / data-hook without
pulling in lxml or BeautifulSoup. It is deliberately forgiving: unclosed tags
and stray end tags (both rife on real pages) never raise.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Elements that never have children; an explicit close tag is optional/absent.
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
# Their text content is code/markup, not page text: never descend for text().
_OPAQUE = {"script", "style", "template", "noscript"}
_WS = re.compile(r"\s+")


class Node:
    """One element. `children` holds child Nodes and bare text strings."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent: Node | None = None

    # -- attribute helpers -------------------------------------------------- #
    @property
    def id(self) -> str | None:
        return self.attrs.get("id")

    def get(self, name: str, default=None):
        return self.attrs.get(name, default)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    # -- traversal ---------------------------------------------------------- #
    def descendants(self):
        """Yield every descendant Node (depth-first), iteratively."""
        stack = [c for c in reversed(self.children) if isinstance(c, Node)]
        while stack:
            node = stack.pop()
            yield node
            for c in reversed(node.children):
                if isinstance(c, Node):
                    stack.append(c)

    def find(self, pred):
        for n in self.descendants():
            if pred(n):
                return n
        return None

    def find_all(self, pred) -> list[Node]:
        return [n for n in self.descendants() if pred(n)]

    def text(self, sep: str = " ") -> str:
        """Concatenated, whitespace-collapsed text of this subtree.

        Skips <script>/<style>/etc. so inline JSON and CSS never leak in.
        """
        result: list[str] = []

        def walk(node: Node):
            for ch in node.children:
                if isinstance(ch, str):
                    result.append(ch)
                elif ch.tag not in _OPAQUE:
                    walk(ch)

        walk(self)
        joined = sep.join(t for t in (s.strip() for s in result) if t)
        return _WS.sub(" ", joined).strip()


# Predicate builders, for readable queries: find(by_hook("review")).
def by_id(value: str):
    return lambda n: n.attrs.get("id") == value


def by_id_prefix(prefix: str):
    return lambda n: (n.attrs.get("id") or "").startswith(prefix)


def by_hook(value: str):
    return lambda n: n.attrs.get("data-hook") == value


def by_class(value: str):
    return lambda n: value in n.classes


def by_attr(name: str, value: str):
    return lambda n: n.attrs.get(name) == value


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        # Close down to the nearest matching open tag; ignore strays. This is
        # what keeps malformed markup from corrupting the rest of the tree.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        if self._stack[-1].tag in _OPAQUE:
            return
        if data.strip():
            self._stack[-1].children.append(data)


def parse(html: str) -> Node:
    """Parse an HTML string into a root Node (its children are the document)."""
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root
