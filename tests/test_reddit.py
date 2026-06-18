"""Reddit extractor: URL matching, .json parsing, and DOM query handling."""

from __future__ import annotations

import json

import pytest

from webextract.base import FetchOptions
from webextract.extractors import reddit as reddit_mod
from webextract.extractors.reddit import RedditExtractor


@pytest.mark.parametrize("url,expected", [
    ("https://www.reddit.com/r/python/comments/1/t/", True),
    ("https://old.reddit.com/r/x/", True),
    ("https://reddit.com", True),            # bare domain, no trailing slash
    ("http://reddit.com/r/x", True),
    ("https://example.com/reddit.com", False),
    ("https://notreddit.com/r/x/", False),
])
def test_matches(url, expected):
    assert RedditExtractor().matches(url) is expected


def _fake_payload():
    post = {
        "title": "Title", "author": "op", "permalink": "/r/x/comments/1/t/",
        "subreddit_name_prefixed": "r/x", "score": 9, "num_comments": 2,
        "post_hint": "self", "selftext": "the body", "url_overridden_by_dest": None,
    }
    comments = {"data": {"children": [
        {"kind": "t1", "data": {
            "author": "u1", "score": 3, "body": "top-level",
            "replies": {"data": {"children": [
                {"kind": "t1", "data": {
                    "author": "u2", "score": 1, "body": "nested", "replies": "",
                }},
            ]}},
        }},
        {"kind": "more", "data": {}},  # must be ignored
    ]}}
    return [
        {"data": {"children": [{"data": post}]}},
        comments,
    ]


def test_json_extract_flattens_threaded_comments(monkeypatch):
    monkeypatch.setattr(
        reddit_mod, "http_get",
        lambda url, accept="text/html": (json.dumps(_fake_payload()), "application/json"),
    )
    data = RedditExtractor().extract(
        "https://www.reddit.com/r/x/comments/1/t/", FetchOptions()
    )
    assert data["title"] == "Title"
    assert data["url"] == "https://www.reddit.com/r/x/comments/1/t/"
    bodies = [(c["body"], c["depth"]) for c in data["comments"]]
    assert bodies == [("top-level", 0), ("nested", 1)]  # "more" stub dropped


def test_json_extract_respects_max_items(monkeypatch):
    monkeypatch.setattr(
        reddit_mod, "http_get",
        lambda url, accept="text/html": (json.dumps(_fake_payload()), "application/json"),
    )
    data = RedditExtractor().extract(
        "https://www.reddit.com/r/x/comments/1/t/", FetchOptions(max_items=1)
    )
    assert len(data["comments"]) == 1


def test_json_extract_wraps_fetch_errors(monkeypatch):
    def boom(url, accept="text/html"):
        raise OSError("blocked")
    monkeypatch.setattr(reddit_mod, "http_get", boom)
    with pytest.raises(RuntimeError, match="firefox"):
        RedditExtractor().extract("https://www.reddit.com/r/x/", FetchOptions())


def test_dom_extract_strips_query_string(monkeypatch):
    captured = {}

    def fake_exec(url, opts, script, **kwargs):
        captured["url"] = url
        return {"title": "T", "permalink": "/r/x/comments/1/t/", "comments": []}

    monkeypatch.setattr(reddit_mod, "firefox_execute", fake_exec)
    RedditExtractor().extract(
        "https://www.reddit.com/r/x/comments/1/t/?sort=top&context=3",
        FetchOptions(firefox=True),
    )
    assert captured["url"] == "https://www.reddit.com/r/x/comments/1/t/"


def test_dom_extract_raises_when_no_post(monkeypatch):
    monkeypatch.setattr(
        reddit_mod, "firefox_execute",
        lambda *a, **k: {"comments": []},  # no title
    )
    with pytest.raises(RuntimeError, match="post content"):
        RedditExtractor().extract(
            "https://www.reddit.com/r/x/", FetchOptions(firefox=True)
        )
