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
webextract <url> --firefox          # render via real Firefox (JS, bot blocks)
webextract <url> --profile logged-in  # use a logged-in Firefox profile
webextract <url> --json             # structured output
webextract --list-extractors
```

Or without installing: `python -m webextract <url>` from the project root.

Programmatic:

```python
from webextract import extract
data = extract("https://example.com")
data = extract("https://www.reddit.com/r/.../", profile="logged-in")
```

Options: `--firefox`, `--profile NAME`, `--no-headless`, `--max-items N`,
`--wait SECONDS`, `--json`.

## Architecture

- `webextract/base.py` - `Extractor` base class, `FetchOptions`, and the registry.
- `webextract/fetch.py` - HTTP and Firefox/Selenium backends, profile resolution.
- `webextract/markdown.py` - shared HTML/DOM to markdown helpers.
- `webextract/cli.py` / `__main__.py` - command line entry point.
- `webextract/extractors/` - one module per site; `generic.py` is the
  always-matches fallback. Each is registered with `@register`.

Dispatch: `get_extractor(url)` returns the highest-`priority` extractor whose
`matches(url)` is true; `generic` (priority -100) catches everything else.

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

## Requirements

`selenium` (for `--firefox`/`--profile`) and a local Firefox install;
geckodriver is fetched automatically by Selenium Manager.
