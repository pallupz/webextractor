# webextract

Extract readable content from a webpage as markdown. Text is preserved;
hyperlinks become `[text](href)` and images become `![alt](src)`. Media is
referenced, never downloaded.

## Purpose & scope

A personal, agentic web reader: point it at a URL and get back clean markdown
or structured data, including for pages that block a plain fetch or render their
content with JavaScript. It is the fallback you reach for when a normal web
fetch returns a login wall, a bot challenge, or an empty JS-only shell. Most
sites go through a generic readability pass; a few (Reddit, Amazon) have
dedicated extractors that return structured fields.

**What it is:**

- A single-user tool for interactive or agentic lookups, one page at a time.
- Reliability-first: it leads with a real browser and reuses a persistent
  profile so cookies and trust build up, because the priority is not getting
  blocked rather than raw speed.
- A clean-data layer: structured output (`--json`) for the dedicated sites, and
  readable markdown for everything else.

**What it is not:**

- Not a bulk scraper or crawler. Renders are serial (the shared browser profile
  is single-instance locked), and it does not spider links, queue jobs, or
  manage proxies/rate limits. Use it at human/agent pace, not for mass data
  collection.
- Not a stable API. It parses live HTML, so fields are best-effort and can shift
  when a site changes its markup; treat it as a reader, not a guaranteed feed.
- Not exhaustive per page. The dedicated extractors capture what the page itself
  serves (e.g. Amazon returns the on-page top reviews, not the full corpus).
- Not a media downloader. Images and video stay as URL references.
- Not a tool for defeating authentication or paywalls. Reusing *your own*
  logged-in profile is supported; circumventing access controls is not the goal.

## Install

```bash
uv sync                             # selenium + the `webextract` command
uv sync --extra mcp                 # ... and the MCP server
```

The `mcp` extra is opt-in: plain-HTTP extraction needs no third-party packages,
so the MCP server's dependencies are only pulled in when you ask for them.

## Usage

```bash
webextract <url>                    # plain HTTP
webextract <url> --browser firefox  # render via real browser (JS, bot blocks)
webextract <url> --browser chrome   # ... or Chrome
webextract <url> --profile logged-in  # use a logged-in browser profile
webextract <url> --scroll --max-items 200  # load more lazy content (e.g. comments)
webextract <url> --json             # structured output
webextract --list-extractors
```

Or without installing: `python -m webextract <url>` from the project root.

Programmatic:

```python
from webextract import extract
data = extract("https://example.com")
data = extract("https://www.reddit.com/r/.../", profile="logged-in", scroll=True, max_items=200)
data = extract("https://example.com", browser="chrome")
```

Options: `--browser {firefox,chrome}`, `--profile NAME`, `--no-headless`,
`--ephemeral`, `--max-items N`, `--scroll`, `--wait SECONDS`, `--json`.

### Persistent browser profile

Every browser render reuses the tool's own profile (under `~/.webextract/
profiles/<engine>`) unless you name one with `--profile`. Reusing one profile
across runs lets cookies and a non-fresh fingerprint accumulate, which is what
keeps bot-protected sites (Amazon, Reddit) from challenging you. It is created
on first use and shared by every site, so it is single-instance locked: render
one page at a time (fine for a personal/agentic tool, not for parallel runs).
Turn it off per run with `--ephemeral`, globally with `WEBEXTRACT_NO_PERSIST=1`,
or override it by pointing `--profile` at a real logged-in profile.

`--browser` renders the page in a real browser (Firefox or Chrome) to run
JavaScript and get past bot blocks, and combines with `--profile`
(e.g. `--browser chrome --profile Work`). `--profile` and `--scroll` also imply
a browser on their own. When no browser is named, an installed one is auto-
picked: the extractor's preferred browser if set (Reddit prefers Firefox),
otherwise Firefox. Naming a browser that is not installed is a clear error, and
if none of the supported browsers are installed you get told to install one.

Note: for Reddit, prefer Firefox; Reddit's bot detection blocks headless Chrome
more aggressively, so the rendered DOM often comes back empty there. The Reddit
extractor auto-selects Firefox for this reason.

### Borrowing a profile's cookies without a browser

`--cookies-from PROFILE` (MCP: `cookies_from_profile`) attaches the cookies a
local Firefox profile holds for the URL to a plain HTTP fetch. No browser
starts and no page JavaScript runs, so it is much faster and far lighter on the
origin than `--browser`. Unlike `--profile` it does not imply a browser, and it
works while that profile is open in a running Firefox (the cookie db is copied
before reading, never written to). Only cookies scoped to the requested host,
path, and scheme are sent, and expired ones are dropped.

Use it for sites whose content is server-rendered but which reject automated
browsers: browse the site normally in that profile, then fetch with this.

Python's own TLS handshake is unmistakably not a browser's, and some stacks
check that against the User-Agent. `--impersonate TARGET` (MCP: `impersonate`)
sends the request through [curl_cffi](https://github.com/lexiforest/curl_cffi)
so the TLS and HTTP/2 handshakes match a real browser too — pair it with
`--cookies-from` and name the browser the cookies came from:

```bash
webextract --cookies-from ~/.mozilla/firefox/logged-in --impersonate firefox135 URL
```

`curl_cffi` is an optional dependency; without it, `--impersonate` errors and
the other paths are unaffected.

Note that none of this beats a stack that has already decided against you.
Akamai records its verdict in the `_abck` cookie (`~-1~` = rejected), and once
a client is in that state every request 403s regardless of handshake or cookie
freshness — including from an ordinary browser. Reputation is per client and
recovers slowly, so a burst of failed attempts makes things durably worse.
Diagnose before iterating: if `_abck` reads `~-1~` after a genuine browser
visit, the request shape is not what is wrong.

Remote HTTP servers ignore this argument, exactly as they ignore `--profile`,
so a remote caller can never read the host's cookie jars.

### Amazon

Amazon product, listing and search pages get a dedicated structured extractor.
It returns clean fields where the generic fallback would bury the content under
the megamenu and ad carousels and mangle Amazon's split-span prices into
garbled duplicates.

To avoid Amazon's bot blocks (a 503/captcha or an empty page, common when
looping or hitting a store from a foreign IP), the extractor **leads with a real
browser** whenever one is installed - it favours reliability over speed - and
only falls back to plain HTTP when no browser is present. If even the browser
comes back blocked, pass `--profile <a logged-in Amazon browser profile>` to
reuse your session, which all but eliminates blocks.

```bash
webextract "https://www.amazon.in/dp/B08T1TVFNX"            # product detail
webextract "https://www.amazon.in/s?k=table+top+dishwasher" # search results
webextract --json "https://www.amazon.in/dp/B08T1TVFNX"     # structured fields
```

A product extract (`type: "amazon_product"`) carries `asin`, a clean `price`,
`rating`/`rating_count`, `feature_bullets`, a `specs` map, the `rating_histogram`,
the AI `review_summary` (blurb + aspect chips), and a `reviews` list (each with
`author`, `rating`, `title`, `date`, `variant`, `verified_purchase`, `body`).
Only the page's top reviews (~8) are returned, not the full corpus. A search
extract (`type: "amazon_search"`) returns deduped `results` with `asin`, `title`,
`price`, `rating`, `rating_count`, and a canonical `url`.

Some listings (variation/"twister" products) defer the buybox price to
client-side JS that only the search/options view shows; there `price` can come
back null even via the browser.

`--scroll` loads lazily-rendered content up to `--max-items` by scrolling to the
bottom and clicking inline "N more replies" buttons. For Reddit this expands the
comment tree; "continue this thread" links that navigate to a separate page are
intentionally not followed, so extremely deep tails may not be fully captured.
Every browser launch is torn down (browser + driver) in a `finally`, so no
browser processes are left behind.

## MCP server

Runs over stdio via the `webextract-mcp` entry point. Exposes a `fetch_page`
tool so an AI client can use this as a fallback when its own web fetch fails or
is blocked.

```bash
uv sync --extra mcp
uv run --extra mcp webextract-mcp            # stdio (Claude Desktop, Claude Code)
uv run --extra mcp webextract-mcp --http     # streamable HTTP at http://127.0.0.1:8000/mcp
```

`--extra mcp` is needed on every `uv run` here, not just the sync: `mcp` is an
optional dependency, and a bare `uv run` resyncs the environment without it.

Set `WEBEXTRACT_PROFILE=logged-in` in the server env to default the Firefox
profile (local servers only). That default applies to *every* call, and a
profile forces a browser render, so pass `profile: "none"` for any page that
does not need the session - otherwise a profile already open in your own
browser makes the call block on the single-instance lock rather than fetch.

**Claude Code** (stdio):

```bash
claude mcp add --scope user webextract \
  -e WEBEXTRACT_PROFILE=logged-in \
  -- uv --directory /Users/pallupz/personal/webextractor run --extra mcp webextract-mcp
```

Remove with `claude mcp remove webextract -s user`.

**Claude Desktop** - add to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "webextract": {
      "command": "uv",
      "args": ["--directory", "/Users/pallupz/personal/webextractor", "run", "--extra", "mcp", "webextract-mcp"],
      "env": { "WEBEXTRACT_PROFILE": "logged-in" }
    }
  }
}
```

Important: **fully quit Claude Desktop (Cmd+Q) before editing this file**, then
reopen it. The app holds the config in memory and rewrites it on quit/launch,
so an edit made while it is running gets overwritten. (Or use the app's
Settings -> Developer -> Edit Config, which reloads safely.)

**ChatGPT** - only talks to remote MCP servers over HTTPS, so run
`webextract-mcp --http` and expose it with a tunnel (e.g.
`cloudflared tunnel --url http://localhost:8000`), then add
`https://<tunnel-host>/mcp` as a custom connector (developer mode). Note: a
tunnel to your own Mac is what keeps the logged-in Firefox profile usable;
a cloud host would only do plain-HTTP and browser fetches without your login.

### Fallback instruction (per client)

The "fallback" behavior is driven by the tool description, not enforced by the
protocol, so reinforce it in each client's instructions:

> When a web fetch/browse fails, is blocked (HTTP 403/429), times out, or
> returns empty or JavaScript-only content, fall back to the `webextract` MCP
> server's `fetch_page` tool. Pass `use_browser=true` for JavaScript-heavy or
> bot-blocked sites (e.g. Reddit).

- **Claude Code:** add the line to `~/.claude/CLAUDE.md` (global) or a
  project `CLAUDE.md`. Remove it by deleting the line.
- **Claude Desktop:** instructions are account-level, not a local file. Add it
  in the app: Settings -> Profile -> "What personal preferences should Claude
  consider in responses?". Remove it by deleting the text from that same field.
  (Cowork / Claude Code sessions launched from Desktop already inherit the
  global `~/.claude/CLAUDE.md`.)

### Removing the server

**Claude Code:**

```bash
claude mcp remove webextract -s user
```

**Claude Desktop** - quit Claude Desktop first (see note above), then delete the
`webextract` entry from `mcpServers` in
`~/Library/Application Support/Claude/claude_desktop_config.json` (remove the
whole `mcpServers` block if it is the only server), then reopen Claude Desktop.

**ChatGPT** - remove the connector in ChatGPT settings and stop the tunnel /
`webextract-mcp --http` process.

## Architecture

- `webextract/base.py` - `Extractor` base class, `FetchOptions`, and the registry.
- `webextract/fetch.py` - plain-HTTP fetch, SSRF guard, and the generic
  (browser-agnostic) Selenium driving: readiness waits, scrolling, `execute`.
- `webextract/browsers/` - one module per browser backend (`firefox.py`,
  `chrome.py`); each builds a Selenium driver and resolves profiles. Registered
  with `@register`, selected by name via `FetchOptions.engine`.
- `webextract/markdown.py` - shared HTML/DOM to markdown helpers.
- `webextract/dom.py` - lenient stdlib HTML element tree (find by id/class/
  data-hook) for extractors that need specific elements, not flattened text.
- `webextract/cli.py` / `__main__.py` - command line entry point.
- `webextract/extractors/` - one module per site; `generic.py` is the
  always-matches fallback. Each is registered with `@register`.

Dispatch: `get_extractor(url)` returns the highest-`priority` extractor whose
`matches(url)` is true; `generic` (priority -100) catches everything else.
A browser, when needed, is picked by name with `get_browser(opts.engine)`.

## Adding a new site

1. Create `webextract/extractors/<site>.py`:

   ```python
   from ..base import Extractor, FetchOptions, register

   @register
   class ExampleExtractor(Extractor):
       name = "example"
       priority = 100

       def matches(self, url: str) -> bool:
           return "example.com" in url

       def extract(self, url: str, opts: FetchOptions) -> dict:
           ...  # return a dict with at least type, url, title
           # optionally override render(self, data) for custom output
   ```

2. Import it in `webextract/extractors/__init__.py` so it registers.

That's it - `main` never changes.

## Adding a browser

Drop a module in `webextract/browsers/` with a `@register`-decorated `Browser`
whose `build_driver(headless, profile)` returns a ready Selenium driver, then
import it in `webextract/browsers/__init__.py`. It becomes selectable as
`--browser <name>`. The generic rendering/scrolling code in `fetch.py` is
browser-agnostic and needs no changes.

## Requirements

`selenium` (for `--browser`/`--profile`/`--scroll`) and a local install of the
chosen browser (Firefox and/or Chrome). The matching driver (geckodriver or
chromedriver) is fetched automatically by Selenium Manager.

## Versioning

The server reports a build id: the `version` in `pyproject.toml` plus the commit
it is actually running from, e.g. `0.2.0+g1a2b3c4`, with `-dirty` appended when
the checkout has uncommitted edits. It appears twice, so a client can always
tell which build answered it:

- `serverInfo.version` in the MCP handshake.
- The last line of the server instructions, which the model sees every session.

**Bump `version` in `pyproject.toml` whenever behaviour changes**, and tag the
release to match (`git tag -a v0.3.0 && git push --tags`). The commit half is
resolved at import time and needs no upkeep; the number is the hand-maintained
part, and without a bump two different builds can only be told apart by sha.

Both halves are read at import time from the checkout the code is running from,
so a `git pull` on the gateway box is enough; no reinstall is needed for the
number to follow. Installed package metadata is only the fallback for a
non-checkout install.

## Tests

```bash
uv sync --extra mcp --extra dev
uv run --extra mcp --extra dev pytest
```
