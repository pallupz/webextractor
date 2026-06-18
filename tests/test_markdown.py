"""HTML -> markdown extraction."""

from __future__ import annotations

from webextract.markdown import html_to_markdown


def test_title_and_text():
    title, text = html_to_markdown(
        "<html><head><title>Hello &amp; Bye</title></head>"
        "<body><p>First.</p><p>Second.</p></body></html>"
    )
    assert title == "Hello & Bye"
    assert "First." in text and "Second." in text


def test_links_preserved_as_markdown():
    _, text = html_to_markdown('<a href="https://x.com">click</a>')
    assert "[click](https://x.com)" in text


def test_images_preserved_as_references():
    _, text = html_to_markdown('<img src="https://x.com/a.png" alt="cat">')
    assert "![cat](https://x.com/a.png)" in text


def test_script_and_style_skipped():
    _, text = html_to_markdown(
        "<body>keep<script>var secret=1;</script>"
        "<style>.x{}</style>this</body>"
    )
    assert "secret" not in text and ".x" not in text
    assert "keep" in text and "this" in text


def test_bare_link_without_text_falls_back_to_href_text():
    # No anchor text and no href -> nothing crashes, empty stays empty.
    _, text = html_to_markdown("<a>nada</a>")
    assert "nada" in text
