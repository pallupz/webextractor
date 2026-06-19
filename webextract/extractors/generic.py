"""Fallback extractor: works for most websites.

Pulls the page's title and readable text, keeping links and images as
markdown references. Use opts.browser for JS-heavy or bot-blocked pages.
"""

from __future__ import annotations

from urllib.error import HTTPError

from ..base import Extractor, FetchOptions, register
from ..fetch import http_get, render_page_source
from ..markdown import html_to_markdown


@register
class GenericExtractor(Extractor):
    name = "generic"
    priority = -100  # always-matches fallback; every site extractor outranks it

    def matches(self, url: str) -> bool:
        return True

    def extract(self, url: str, opts: FetchOptions) -> dict:
        if opts.use_browser:
            html, content_type = render_page_source(url, self.resolve_browser(opts))
        else:
            try:
                html, content_type = http_get(url)
            except HTTPError as e:
                if e.code in (401, 403, 405, 406, 429) or e.code >= 500:
                    raise RuntimeError(
                        f"plain HTTP fetch was blocked (HTTP {e.code}); "
                        "retry with use_browser=true (--browser firefox) to "
                        "render in a real browser"
                    ) from e
                raise
        title, text = html_to_markdown(html)
        return {
            "type": "webpage",
            "extractor": self.name,
            "url": url,
            "content_type": content_type,
            "title": title,
            "text": text,
        }
