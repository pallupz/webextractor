"""Reddit extractor.

Prefers a real browser (the .json endpoint is often blocked by Reddit's
network security, while the JS-challenged HTML page loads fine). Reads the
shreddit DOM into a structured post + threaded comments, keeping inline links
and images and the post's primary media (image / gallery / video / link) as
markdown references. Falls back to the .json API when no browser is requested.

Anything that is not a post permalink (search results, subreddit and front-page
feeds) is read as a listing: one line per post rather than a single post.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from ..base import Extractor, FetchOptions, register
from ..fetch import http_get, render_execute
from ..markdown import JS_MARKDOWN_FN

# Built in the browser: md() (from JS_MARKDOWN_FN) + the post's primary media.
_REDDIT_DOM_SCRIPT = JS_MARKDOWN_FN + r"""
function postMedia(post) {
  const type = post.getAttribute('post-type') || '';
  const href = post.getAttribute('content-href');
  const domain = post.getAttribute('domain') || '';
  const mc = post.querySelector('[slot="post-media-container"]');
  if (type === 'image') {
    return href ? '![image](' + href + ')' : '';
  }
  if (type === 'gallery') {
    const seen = new Set(), lines = [];
    if (mc) for (const i of mc.querySelectorAll('img[src]')) {
      const u = i.getAttribute('src'), key = u.split('?')[0];
      if (!seen.has(key)) { seen.add(key); lines.push('![image](' + u + ')'); }
    }
    return lines.join('\n');
  }
  if (type.includes('video')) {
    let v = null;
    if (mc) {
      const el = mc.querySelector('video[src],source[src],shreddit-player,shreddit-player-2');
      if (el) v = el.getAttribute('src');
    }
    return '(video: ' + (v || href || '') + ')';
  }
  if (type === 'link' && href) {
    return '[' + (domain || href) + '](' + href + ')';
  }
  return '';
}

const post = document.querySelector('shreddit-post');
const bodyEl = post && post.querySelector('[slot="text-body"]');
const media = post ? postMedia(post) : '';
const body = bodyEl ? md(bodyEl) : '';
const selftext = [media, body].filter(Boolean).join('\n\n') || null;
const domain = post && (post.getAttribute('domain') || '');
const href = post && post.getAttribute('content-href');

const comments = [...document.querySelectorAll('shreddit-comment')].map(c => {
  const el = c.querySelector('[slot="comment"]');
  return {
    author: c.getAttribute('author'),
    score: parseInt(c.getAttribute('score')) || null,
    depth: parseInt(c.getAttribute('depth')) || 0,
    body: el ? md(el) : ''
  };
});
return {
  title: post && post.getAttribute('post-title'),
  author: post && post.getAttribute('author'),
  subreddit: post && post.getAttribute('subreddit-prefixed-name'),
  score: post && (parseInt(post.getAttribute('score')) || null),
  num_comments: post && (parseInt(post.getAttribute('comment-count')) || null),
  permalink: post && post.getAttribute('permalink'),
  post_type: post && post.getAttribute('post-type'),
  link: (domain && !domain.startsWith('self.') && href) ? href : null,
  selftext,
  comments
};
"""


# Ready when the post has rendered AND (it has no comments OR the comment
# section has hydrated). Survives Reddit's JS challenge, which delays render.
_REDDIT_READY = (
    "const p = document.querySelector('shreddit-post');"
    "if (!p) return false;"
    "const cc = parseInt(p.getAttribute('comment-count')) || 0;"
    "return cc === 0 || document.querySelector('shreddit-comment') != null;"
)

# Click inline "N more replies" expander BUTTONS so nested replies load in place.
# Only <button>s: the equivalent <a> links navigate away to a single-thread page.
_REDDIT_LOAD_MORE = (
    "const re = /more (repl|comment)/i;"
    "const btns = [...document.querySelectorAll('button')]"
    "  .filter(b => re.test(b.textContent || ''));"
    "btns.slice(0, 20).forEach(b => { try { b.click(); } catch (e) {} });"
)

# Search results render as search-post-unit blocks; feeds as shreddit-post.
_LISTING_SELECTOR = '[data-testid="search-post-unit"], shreddit-post'

_REDDIT_LISTING_SCRIPT = r"""
const abs = h => h ? new URL(h, location.origin).href : null;
const num = v => { const n = parseInt(v); return isNaN(n) ? null : n; };
const posts = [];
for (const u of document.querySelectorAll('[data-testid="search-post-unit"]')) {
  const a = u.querySelector('a[data-testid="post-title"]');
  if (!a) continue;
  let ctx = {};
  try {
    const t = u.querySelector('search-telemetry-tracker');
    ctx = JSON.parse(t.getAttribute('data-faceplate-tracking-context')) || {};
  } catch (e) {}
  const ts = u.querySelector('faceplate-timeago');
  const counts = [...u.querySelectorAll('[data-testid="search-counter-row"] faceplate-number')]
    .map(n => num(n.getAttribute('number')));
  posts.push({
    title: (a.getAttribute('aria-label') || a.textContent).replace(/\s+/g, ' ').trim(),
    url: abs(a.getAttribute('href')),
    subreddit: ctx.subreddit && ctx.subreddit.name ? 'r/' + ctx.subreddit.name : null,
    author: ctx.profile ? ctx.profile.name : null,
    created: ts ? ts.getAttribute('ts') : null,
    score: counts.length > 0 ? counts[0] : null,
    num_comments: counts.length > 1 ? counts[1] : null,
    link: null
  });
}
for (const p of document.querySelectorAll('shreddit-post')) {
  const domain = p.getAttribute('domain') || '';
  const href = p.getAttribute('content-href');
  posts.push({
    title: p.getAttribute('post-title'),
    url: abs(p.getAttribute('permalink')),
    subreddit: p.getAttribute('subreddit-prefixed-name'),
    author: p.getAttribute('author'),
    created: p.getAttribute('created-timestamp'),
    score: num(p.getAttribute('score')),
    num_comments: num(p.getAttribute('comment-count')),
    link: (domain && !domain.startsWith('self.') && href) ? href : null
  });
}
return {title: document.title, posts};
"""


def _is_post(url: str) -> bool:
    return "/comments/" in urlsplit(url).path


def _to_www(url: str) -> str:
    # old.reddit has a different DOM; the same paths and queries work on www.
    parts = urlsplit(url)
    if parts.netloc.lower() == "old.reddit.com":
        parts = parts._replace(netloc="www.reddit.com")
    return urlunsplit(parts)


@register
class RedditExtractor(Extractor):
    name = "reddit"
    priority = 100
    # Reddit's bot detection blocks headless Chrome far more than Firefox, so
    # prefer Firefox when the caller did not explicitly pick a browser.
    preferred_browsers = ("firefox",)

    def matches(self, url: str) -> bool:
        return re.search(r"https?://(\w+\.)?reddit\.com(/|$)", url) is not None

    def extract(self, url: str, opts: FetchOptions) -> dict:
        url = _to_www(url)
        if not _is_post(url):
            if opts.use_browser:
                return self._extract_listing_dom(url, opts)
            return self._extract_listing_json(url, opts)
        if opts.use_browser:
            return self._extract_dom(url, opts)
        return self._extract_json(url, opts)

    # -- Firefox / rendered DOM (preferred) -------------------------------- #

    def _extract_dom(self, url: str, opts: FetchOptions) -> dict:
        # Drop the query string: Reddit's sort/context params don't change the
        # shreddit DOM we read, and a bare permalink renders most reliably.
        data = render_execute(
            url.split("?")[0], self.resolve_browser(opts), _REDDIT_DOM_SCRIPT,
            ready_js=_REDDIT_READY,
            scroll_count_js="return document.querySelectorAll('shreddit-comment').length;",
            scroll_target=opts.max_items,
            scroll_more_js=_REDDIT_LOAD_MORE,
        )
        if not data or not data.get("title"):
            raise RuntimeError("could not find post content in rendered page")
        permalink = data.get("permalink") or ""
        comments = [c for c in data.get("comments", []) if c.get("body")]
        return {
            "type": "reddit_post",
            "extractor": self.name,
            "url": "https://www.reddit.com" + permalink if permalink else url,
            "title": data.get("title"),
            "author": data.get("author"),
            "subreddit": data.get("subreddit"),
            "score": data.get("score"),
            "num_comments": data.get("num_comments"),
            "post_type": data.get("post_type"),
            "selftext": data.get("selftext") or None,
            "link": data.get("link"),
            "comments": comments[: opts.max_items],
        }

    # -- .json API (fallback when no browser requested) -------------------- #

    def _extract_json(self, url: str, opts: FetchOptions) -> dict:
        json_url = url.split("?")[0].rstrip("/") + ".json"
        try:
            raw, _ = http_get(json_url, accept="application/json")
            payload = json.loads(raw)
            post = payload[0]["data"]["children"][0]["data"]
        except Exception as e:
            raise RuntimeError(
                f"Reddit .json fetch failed ({e}); retry with --firefox/--profile"
            ) from e

        def walk(children, depth=0):
            out = []
            for child in children:
                if child.get("kind") != "t1":
                    continue
                c = child["data"]
                out.append({
                    "author": c.get("author"),
                    "score": c.get("score"),
                    "depth": depth,
                    "body": c.get("body"),  # already markdown
                })
                replies = c.get("replies")
                if isinstance(replies, dict):
                    out.extend(walk(replies["data"]["children"], depth + 1))
            return out

        comments = walk(payload[1]["data"]["children"]) if len(payload) > 1 else []
        link = post.get("url_overridden_by_dest")
        return {
            "type": "reddit_post",
            "extractor": self.name,
            "url": "https://www.reddit.com" + post.get("permalink", ""),
            "title": post.get("title"),
            "author": post.get("author"),
            "subreddit": post.get("subreddit_name_prefixed"),
            "score": post.get("score"),
            "num_comments": post.get("num_comments"),
            "post_type": post.get("post_hint"),
            "selftext": post.get("selftext") or (f"![image]({link})" if link else None),
            "link": link,
            "comments": comments[: opts.max_items],
        }

    # -- listings: search results and feeds -------------------------------- #

    def _extract_listing_dom(self, url: str, opts: FetchOptions) -> dict:
        # Keep the query string here: it carries the search terms and sort.
        data = render_execute(
            url, self.resolve_browser(opts), _REDDIT_LISTING_SCRIPT,
            ready_js=f"return document.querySelector('{_LISTING_SELECTOR}') != null;",
            scroll_count_js=f"return document.querySelectorAll('{_LISTING_SELECTOR}').length;",
            scroll_target=opts.max_items,
        ) or {}
        return self._listing(url, data.get("title"), data.get("posts") or [], opts)

    def _extract_listing_json(self, url: str, opts: FetchOptions) -> dict:
        parts = urlsplit(url)
        json_url = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/.json"))
        try:
            raw, _ = http_get(json_url, accept="application/json")
            children = json.loads(raw)["data"]["children"]
        except Exception as e:
            raise RuntimeError(
                f"Reddit .json fetch failed ({e}); retry with --firefox/--profile"
            ) from e
        posts = []
        for child in children:
            if child.get("kind") != "t3":
                continue
            p = child["data"]
            created = p.get("created_utc")
            posts.append({
                "title": p.get("title"),
                "url": "https://www.reddit.com" + p.get("permalink", ""),
                "subreddit": p.get("subreddit_name_prefixed"),
                "author": p.get("author"),
                "created": (datetime.fromtimestamp(created, timezone.utc).isoformat()
                            if created is not None else None),
                "score": p.get("score"),
                "num_comments": p.get("num_comments"),
                "link": None if p.get("is_self") else p.get("url_overridden_by_dest"),
            })
        return self._listing(url, None, posts, opts)

    def _listing(self, url: str, title: str | None, posts: list, opts: FetchOptions) -> dict:
        seen, unique = set(), []
        for p in posts:
            if p.get("url") and p["url"] not in seen:
                seen.add(p["url"])
                unique.append(p)
        return {
            "type": "reddit_listing",
            "extractor": self.name,
            "url": url,
            "title": title,
            "posts": unique[: opts.max_items],
        }

    # -- rendering --------------------------------------------------------- #

    def render(self, data: dict) -> str:
        if data.get("type") == "reddit_listing":
            return self._render_listing(data)
        lines = [f"# {data['title']}\n"]
        lines.append(
            f"{data.get('subreddit')} | u/{data.get('author')} | "
            f"score {data.get('score')} | {data.get('num_comments')} comments"
        )
        lines.append(data["url"])
        if data.get("link"):
            lines.append(f"Link: {data['link']}")
        if data.get("selftext"):
            lines.append("\n" + data["selftext"])
        if data.get("comments"):
            lines.append("\n--- Comments ---")
            for c in data["comments"]:
                indent = "  " * c.get("depth", 0)
                lines.append(f"\n{indent}u/{c.get('author')} ({c.get('score')}):")
                for line in (c.get("body") or "").splitlines():
                    lines.append(f"{indent}{line}")
        return "\n".join(lines)

    def _render_listing(self, data: dict) -> str:
        lines = [f"# {data.get('title') or 'Reddit'}\n", data["url"]]
        if not data["posts"]:
            lines.append("\nNo posts found.")
        for i, p in enumerate(data["posts"], 1):
            meta = [
                p.get("subreddit"),
                p.get("author") and f"u/{p['author']}",
                p.get("created") and p["created"][:10],
                p.get("score") is not None and f"score {p['score']}",
                p.get("num_comments") is not None and f"{p['num_comments']} comments",
            ]
            lines.append(f"\n{i}. {p.get('title')}")
            lines.append("   " + " | ".join(m for m in meta if m))
            lines.append(f"   {p['url']}")
            if p.get("link"):
                lines.append(f"   Link: {p['link']}")
        return "\n".join(lines)
