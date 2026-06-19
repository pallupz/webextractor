"""Reddit extractor.

Prefers a real browser (the .json endpoint is often blocked by Reddit's
network security, while the JS-challenged HTML page loads fine). Reads the
shreddit DOM into a structured post + threaded comments, keeping inline links
and images and the post's primary media (image / gallery / video / link) as
markdown references. Falls back to the .json API when no browser is requested.
"""

from __future__ import annotations

import json
import re

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


@register
class RedditExtractor(Extractor):
    name = "reddit"
    priority = 100

    def matches(self, url: str) -> bool:
        return re.search(r"https?://(\w+\.)?reddit\.com(/|$)", url) is not None

    def extract(self, url: str, opts: FetchOptions) -> dict:
        if opts.use_browser:
            return self._extract_dom(url, opts)
        return self._extract_json(url, opts)

    # -- Firefox / rendered DOM (preferred) -------------------------------- #

    def _extract_dom(self, url: str, opts: FetchOptions) -> dict:
        # Drop the query string: Reddit's sort/context params don't change the
        # shreddit DOM we read, and a bare permalink renders most reliably.
        data = render_execute(
            url.split("?")[0], opts, _REDDIT_DOM_SCRIPT,
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

    # -- rendering --------------------------------------------------------- #

    def render(self, data: dict) -> str:
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
