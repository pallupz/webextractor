"""Amazon extractor: structured product detail, reviews, and search results.

Amazon's product pages render server-side, so plain HTTP is enough; the generic
fallback would work but buries the signal under the megamenu, ad carousels, and
Amazon's split-span prices (which flatten into garbled, duplicated numbers).
This extractor reads the stable element anchors (#productTitle, the buybox
`.a-offscreen` price, each `[data-hook="review"]` block, search-result recipes)
into clean structured fields.

Three page shapes are handled:
  * product detail   /dp/<ASIN>, /gp/product/<ASIN>   -> type "amazon_product"
  * search results   /s?k=...                          -> type "amazon_search"
A browser is never required, but `use_browser` is honored for blocked regions.
"""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from urllib.error import HTTPError

from ..base import Extractor, FetchOptions, register
from ..browsers import available, resolve_engine
from ..dom import (Node, by_attr, by_class, by_hook, by_id, by_id_prefix,
                   parse)
from ..fetch import http_get, render_page_source

_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})")
_STARS_RE = re.compile(r"([0-5](?:\.\d)?)\s+out of 5 stars")
_INT_RE = re.compile(r"[\d,]+")
# HTTP statuses Amazon serves to bot-suspected / rate-limited clients.
_BLOCKING_CODES = {403, 429, 503}


def _num(text: str | None) -> int | None:
    """First integer in `text` (commas stripped), or None."""
    if not text:
        return None
    m = _INT_RE.search(text)
    return int(m.group().replace(",", "")) if m else None


def _stars(text: str | None) -> float | None:
    m = _STARS_RE.search(text or "")
    return float(m.group(1)) if m else None


@register
class AmazonExtractor(Extractor):
    name = "amazon"
    priority = 100

    def matches(self, url: str) -> bool:
        return re.search(r"https?://(www\.)?amazon\.[a-z.]+/", url) is not None

    def extract(self, url: str, opts: FetchOptions) -> dict:
        is_search = self._is_search_url(url)
        html = self._get_html(url, opts, is_search)
        root = parse(html)
        if is_search:
            return self._extract_search(url, root, opts)
        return self._extract_product(url, root, opts)

    @staticmethod
    def _is_search_url(url: str) -> bool:
        return "/s?" in url or "/s/" in url or re.search(r"[?&]k=", url) is not None

    def _get_html(self, url: str, opts: FetchOptions, is_search: bool) -> str:
        """Fetch page HTML, leading with a real browser to avoid bot blocks.

        Amazon serves a 503/captcha (or a 200 with no usable content) to plain
        HTTP it suspects of being a bot - common when looping over results or
        hitting a store from a foreign IP. A real browser runs JS and carries a
        real fingerprint, so it is far less likely to be blocked. This tool
        favours not-getting-blocked over speed, so it renders in a browser
        whenever one is installed and only falls back to plain HTTP when none
        is. Repeated failed plain-HTTP hits are exactly what escalates a soft
        block into a hard one, so we avoid making them at all.
        """
        if opts.use_browser:  # explicit --browser/--profile/--scroll
            return render_page_source(url, self.resolve_browser(opts))[0]

        if available():
            engine = resolve_engine(opts.browser, self.preferred_browsers)
            # A persistent browser profile (so cookies build trust) is applied
            # in the render layer; we just pick the engine here.
            html = render_page_source(url, replace(opts, browser=engine))[0]
            if self._looks_blocked(html, is_search):
                print(f"webextract: amazon page still looked blocked via {engine}; "
                      "pass --profile <a logged-in Amazon browser profile> to use "
                      "your session", file=sys.stderr)
            return html

        # No browser installed: best-effort plain HTTP.
        try:
            return http_get(url)[0]
        except HTTPError as e:
            if e.code in _BLOCKING_CODES or e.code >= 500:
                raise RuntimeError(
                    f"Amazon blocked the request (HTTP {e.code}) and no browser "
                    "is installed to fall back to; install Firefox or Chrome."
                ) from e
            raise

    @staticmethod
    def _looks_blocked(html: str, is_search: bool) -> bool:
        """True if the page lacks the anchor real content always carries.

        One check covers every block shape - captcha, the "Sorry" interstitial,
        a 503 body, or an empty rate-limited page - since none of them contain
        the product title (detail) or any result card (search).
        """
        marker = "s-search-result" if is_search else 'id="productTitle"'
        return marker not in html

    # -- product detail ----------------------------------------------------- #

    def _extract_product(self, url: str, root: Node, opts: FetchOptions) -> dict:
        title_el = root.find(by_id("productTitle"))
        if not title_el:
            raise RuntimeError(
                "no product title found; not a recognised Amazon detail page "
                "(or the page was blocked - retry with use_browser=true)"
            )
        m = _ASIN_RE.search(url)
        rating_el = root.find(by_id("acrPopover"))
        count_el = root.find(by_id("acrCustomerReviewText"))
        return {
            "type": "amazon_product",
            "extractor": self.name,
            "url": url,
            "asin": m.group(1) if m else None,
            "title": title_el.text(),
            "price": self._price(root),
            "rating": _stars(rating_el.get("title")) if rating_el else None,
            "rating_count": _num(count_el.text()) if count_el else None,
            "feature_bullets": self._bullets(root),
            "specs": self._specs(root),
            "review_summary": self._review_summary(root),
            "rating_histogram": self._histogram(root),
            "reviews": self._reviews(root, opts.max_items),
        }

    def _price(self, root: Node) -> str | None:
        """Clean price from the buybox, dodging the bogus per-unit duplicate.

        Amazon splits the price across symbol/whole/fraction spans plus a hidden
        `.a-offscreen` copy holding the whole value; reading that copy *inside
        the core-price container* gives one correct string instead of the
        flattened "₹28,119.00 ... ₹28,11,900.00 /100 g" the generic path emits.
        """
        for prefix in ("corePriceDisplay", "corePrice_feature", "apex_desktop",
                       "corePrice"):
            container = root.find(by_id_prefix(prefix))
            if container:
                off = container.find(by_class("a-offscreen"))
                if off and off.text():
                    return off.text()
        off = root.find(by_class("a-offscreen"))
        return (off.text() or None) if off else None

    def _bullets(self, root: Node) -> list[str]:
        # #feature-bullets is the classic layout; newer listings (e.g. fashion)
        # put the same "About this item" list under productFactsDesktopExpander.
        fb = (root.find(by_id("feature-bullets"))
              or root.find(by_id("productFactsDesktopExpander")))
        if not fb:
            return []
        seen, out = set(), []
        for li in fb.find_all(lambda n: n.tag == "li"):
            t = li.text()
            if t and t not in seen and "See more product details" not in t:
                seen.add(t)
                out.append(t)
        return out

    def _specs(self, root: Node) -> dict[str, str]:
        """Key/value specs from the detail tables (th/td) and detail bullets.

        Scoped to the product-information / detail-bullet containers so the
        returns-policy and offer tables elsewhere on the page don't bleed in.
        """
        specs: dict[str, str] = {}
        skip = {"Customer Reviews"}  # redundant with rating/rating_count fields
        containers = root.find_all(
            lambda n: (n.attrs.get("id") or "") in (
                "prodDetails", "productDetails_feature_div",
                "detailBullets_feature_div", "detailBulletsWrapper_feature_div",
            )
        )
        for c in containers:
            for tr in c.find_all(lambda n: n.tag == "tr"):
                th = tr.find(lambda n: n.tag == "th")
                td = tr.find(lambda n: n.tag == "td")
                if th and td:
                    key, val = _clean_key(th.text()), td.text()
                    if key and val and key not in specs and key not in skip:
                        specs[key] = val
            # detailBullets uses <li><span><span bold>Key:</span> Value</span></li>
            for li in c.find_all(lambda n: n.tag == "li"):
                bold = li.find(by_class("a-text-bold"))
                if not bold:
                    continue
                litext, boldtext = li.text(), bold.text()
                val = litext[len(boldtext):] if litext.startswith(boldtext) else litext
                key, val = _clean_key(boldtext), _clean_key(val)
                if key and val and key not in specs and key not in skip:
                    specs[key] = val
        return specs

    def _review_summary(self, root: Node) -> dict | None:
        """Amazon's AI "Customers say" blurb plus its aspect chips.

        The structured aspect buttons (which carry POSITIVE/NEGATIVE sentiment)
        only exist in a JS-hydrated template, not the served DOM, so this reads
        the rendered text instead: the paragraph after the "Customers say"
        heading, then the "Aspect (count)" chips that follow it. Best-effort and
        English-only; absent on the plain-HTTP path for some locales.
        """
        h = root.find(lambda n: n.tag in ("h2", "h3")
                      and n.text().strip() == "Customers say")
        if not h:
            return None
        anc = h
        for _ in range(6):  # climb until the chips are in scope
            if not anc.parent:
                break
            anc = anc.parent
            if "Select to learn more" in anc.text() or re.search(r"\(\d+\)", anc.text()):
                break
        body = anc.text().split("Customers say", 1)[-1].strip()
        if "Select to learn more" in body:
            summary, _, chips = body.partition("Select to learn more")
        else:  # split at the first "Aspect (count)" chip
            m = re.search(r"[A-Za-z][\w ]+ \(\d+\)", body)
            summary, chips = (body[:m.start()], body[m.start():]) if m else (body, "")
        aspects = [{"aspect": a.strip(), "mentions": int(c)}
                   for a, c in re.findall(r"([A-Za-z][A-Za-z ]+?) \((\d+)\)", chips)]
        # drop the "AI Generated from the text of customer reviews" trailer
        summary = re.split(r"\s*AI Generated", summary, 1)[0].strip()
        if not summary and not aspects:
            return None
        return {"summary": summary or None, "aspects": aspects}

    def _histogram(self, root: Node) -> dict[str, int]:
        """Percentage of ratings per star, e.g. {"5": 74, "4": 12, ...}.

        #histogramTable is a <ul>; each row carries its bar percentage in an
        aria-valuenow attribute, in descending star order (5 down to 1).
        """
        ul = root.find(by_id("histogramTable"))
        if not ul:
            return {}
        pcts = [n.attrs["aria-valuenow"]
                for li in ul.find_all(lambda n: n.tag == "li")
                for n in [li.find(lambda x: "aria-valuenow" in x.attrs)] if n]
        hist: dict[str, int] = {}
        for star, pct in zip(("5", "4", "3", "2", "1"), pcts):
            try:
                hist[star] = int(pct)
            except ValueError:
                pass
        return hist

    def _reviews(self, root: Node, limit: int) -> list[dict]:
        blocks = root.find_all(by_hook("review"))
        out = []
        for b in blocks[:limit]:
            star_el = b.find(by_hook("review-star-rating")) or b.find(
                by_hook("cmps-review-star-rating"))
            title_el = b.find(by_hook("reviewTitle")) or b.find(
                by_hook("review-title"))
            author_el = b.find(by_class("a-profile-name"))
            date_el = b.find(by_hook("review-date"))
            body_el = b.find(by_hook("reviewText")) or b.find(
                by_hook("review-body"))
            variant_el = b.find(by_hook("format-strip"))
            verified = b.find(by_hook("avp-badge")) is not None
            helpful_el = b.find(by_hook("helpful-vote-statement"))
            title = title_el.text() if title_el else None
            if title:  # the title node sometimes leads with the star rating
                title = _STARS_RE.sub("", title).strip(" .-")
            out.append({
                "id": b.id,
                "author": author_el.text() if author_el else None,
                "rating": _stars(star_el.text()) if star_el else None,
                "title": title or None,
                "date": date_el.text() if date_el else None,
                "variant": variant_el.text() if variant_el else None,
                "verified_purchase": verified,
                "helpful_votes": _num(helpful_el.text()) if helpful_el else None,
                "body": _clean_body(body_el) if body_el else None,
            })
        return out

    # -- search results ----------------------------------------------------- #

    def _extract_search(self, url: str, root: Node, opts: FetchOptions) -> dict:
        m = re.search(r"[?&]k=([^&]+)", url)
        query = m.group(1).replace("+", " ") if m else None
        from urllib.parse import unquote
        results = []
        seen: set[str] = set()
        cards = root.find_all(by_attr("data-component-type", "s-search-result"))
        for card in cards:
            asin = card.get("data-asin")
            if not asin or asin in seen:  # drop the sponsored/organic dupes
                continue
            seen.add(asin)
            if len(results) >= opts.max_items:
                break
            recipe = card.find(by_attr("data-cy", "title-recipe"))
            # The <h2> holds just the title; the recipe text also carries the
            # "Sponsored" badge and ad-disclaimer popover, so prefer the <h2>.
            title_el = recipe.find(lambda n: n.tag == "h2") if recipe else None
            title_el = title_el or recipe
            price_off = card.find(lambda n: "a-price" in n.classes and
                                  n.find(by_class("a-offscreen")))
            rating_el = card.find(lambda n: "out of 5 stars" in (
                n.get("aria-label") or ""))
            count_el = card.find(lambda n: re.match(
                r"[\d,]+\s+ratings?", n.get("aria-label") or ""))
            results.append({
                "asin": asin,
                "title": title_el.text() if title_el else None,
                "price": (price_off.find(by_class("a-offscreen")).text()
                          if price_off else None),
                "rating": _stars(rating_el.get("aria-label")) if rating_el else None,
                "rating_count": _num(count_el.get("aria-label")) if count_el else None,
                "url": f"https://{_host(url)}/dp/{asin}",
            })
        return {
            "type": "amazon_search",
            "extractor": self.name,
            "url": url,
            "query": unquote(query) if query else None,
            "result_count": len(results),
            "results": results,
        }

    # -- rendering ---------------------------------------------------------- #

    def render(self, data: dict) -> str:
        if data.get("type") == "amazon_search":
            return self._render_search(data)
        return self._render_product(data)

    def _render_product(self, d: dict) -> str:
        lines = [f"# {d['title']}\n", d["url"]]
        meta = []
        if d.get("price"):
            meta.append(f"Price: {d['price']}")
        if d.get("rating") is not None:
            meta.append(f"Rating: {d['rating']} ({d.get('rating_count')} ratings)")
        if d.get("asin"):
            meta.append(f"ASIN: {d['asin']}")
        if meta:
            lines.append(" | ".join(meta))
        if d.get("feature_bullets"):
            lines.append("\n## About this item")
            lines += [f"- {b}" for b in d["feature_bullets"]]
        if d.get("specs"):
            lines.append("\n## Details")
            lines += [f"- {k}: {v}" for k, v in d["specs"].items()]
        rs = d.get("review_summary")
        if rs:
            lines.append("\n## Customers say")
            if rs.get("summary"):
                lines.append(rs["summary"])
            if rs.get("aspects"):
                tags = ", ".join(
                    f"{a['aspect']} ({a['mentions']})" if a.get("mentions")
                    else a["aspect"] for a in rs["aspects"])
                lines.append(f"Aspects: {tags}")
        if d.get("rating_histogram"):
            h = d["rating_histogram"]
            lines.append("\n## Rating breakdown")
            lines.append(" | ".join(f"{s}star {h[s]}%"
                                    for s in sorted(h, reverse=True)))
        if d.get("reviews"):
            lines.append(f"\n## Reviews ({len(d['reviews'])})")
            for r in d["reviews"]:
                head = f"{r.get('author') or 'Anonymous'} - {r.get('rating')}/5"
                if r.get("verified_purchase"):
                    head += " [Verified]"
                lines.append(f"\n### {r.get('title') or ''}".rstrip())
                lines.append(head)
                if r.get("date"):
                    lines.append(r["date"])
                if r.get("body"):
                    lines.append(r["body"])
        return "\n".join(lines)

    def _render_search(self, d: dict) -> str:
        lines = [f"# Amazon search: {d.get('query')}\n", d["url"],
                 f"{d['result_count']} results\n"]
        for i, r in enumerate(d["results"], 1):
            bits = [f"{i}. {r.get('title') or r['asin']}"]
            if r.get("price"):
                bits.append(f"  {r['price']}")
            if r.get("rating") is not None:
                bits.append(f"  {r['rating']}/5 ({r.get('rating_count')})")
            bits.append(f"  {r['url']}")
            lines.append("\n".join(bits))
        return "\n".join(lines)


def _clean_key(text: str) -> str:
    return text.replace("‎", "").replace("‏", "").strip(" :\t")


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or "www.amazon.com"


# a11y teaser cues Amazon wraps every review body in; never part of the review.
_BODY_NOISE = (
    "Brief content visible, double tap to read full content.",
    "Full content visible, double tap to read brief content.",
)


def _clean_body(body_el: Node) -> str | None:
    """Review text with Amazon's collapse/expand teaser cues stripped."""
    # The expand card holds just the prose; fall back to the whole container.
    card = body_el.find(by_class("a-cardui")) or body_el
    text = card.text()
    for noise in _BODY_NOISE:
        text = text.replace(noise, "")
    text = re.sub(r"\s*Read more\s*Read less\s*$", "", text)
    return text.strip() or None
