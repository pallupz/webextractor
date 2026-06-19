# webextract

Extract readable content from a webpage as markdown. Text is preserved;
hyperlinks become `[text](href)` and images become `![alt](src)`. Media is
referenced, never downloaded.

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e .          # installs selenium + the `webextract` command
```

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
`--max-items N`, `--scroll`, `--wait SECONDS`, `--json`.

`--browser` renders the page in a real browser (Firefox or Chrome) to run
JavaScript and get past bot blocks. `--profile` and `--scroll` imply a browser
(defaulting to Firefox). Note: for Reddit, prefer Firefox; Reddit's bot
detection blocks headless Chrome more aggressively, so the rendered DOM often
comes back empty there.

`--scroll` loads lazily-rendered content up to `--max-items` by scrolling to the
bottom and clicking inline "N more replies" buttons. For Reddit this expands the
comment tree; "continue this thread" links that navigate to a separate page are
intentionally not followed, so extremely deep tails may not be fully captured.
Every browser launch is torn down (browser + driver) in a `finally`, so no
browser processes are left behind.

## MCP server

Exposes a `fetch_page` tool so an AI client can use this as a fallback when its
own web fetch fails or is blocked. Install the extra and run:

```bash
.venv/bin/pip install -e ".[mcp]"
webextract-mcp                 # stdio (Claude Desktop, Claude Code)
webextract-mcp --http          # streamable HTTP at http://127.0.0.1:8000/mcp
```

Set `WEBEXTRACT_PROFILE=logged-in` in the server env to default the Firefox
profile (local servers only).

**Claude Code** (stdio):

```bash
claude mcp add webextract -s user \
  -e WEBEXTRACT_PROFILE=logged-in \
  -- /Users/pallupz/personal/webextractor/.venv/bin/webextract-mcp
```

**Claude Desktop** - add to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "webextract": {
      "command": "/Users/pallupz/personal/webextractor/.venv/bin/webextract-mcp",
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
