"""Fallback extractor: works for most websites.

Pulls the page's title and readable text, keeping links and images as
markdown references. Use opts.firefox for JS-heavy or bot-blocked pages.
"""

from __future__ import annotations

from ..base import Extractor, FetchOptions, register
from ..fetch import firefox_page_source, http_get
from ..markdown import html_to_markdown


@register
class GenericExtractor(Extractor):
    name = "generic"
    priority = -100  # always-matches fallback; every site extractor outranks it

    def matches(self, url: str) -> bool:
        return True

    def extract(self, url: str, opts: FetchOptions) -> dict:
        if opts.use_browser:
            html, content_type = firefox_page_source(url, opts)
        else:
            html, content_type = http_get(url)
        title, text = html_to_markdown(html)
        return {
            "type": "webpage",
            "extractor": self.name,
            "url": url,
            "content_type": content_type,
            "title": title,
            "text": text,
        }
