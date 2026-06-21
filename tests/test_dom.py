"""The lenient DOM parser: queries, text extraction, malformed-markup recovery."""

from __future__ import annotations

from webextract.dom import (by_class, by_hook, by_id, by_id_prefix, parse)


def test_find_by_id_and_text():
    root = parse('<div id="a"><span>Hello</span> <b>world</b></div>')
    node = root.find(by_id("a"))
    assert node is not None
    assert node.text() == "Hello world"


def test_text_skips_scripts_and_styles():
    root = parse('<div id="x">keep<script>var a=1<2;</script>'
                 '<style>.c{color:red}</style>more</div>')
    assert root.find(by_id("x")).text() == "keep more"


def test_void_elements_do_not_swallow_siblings():
    # <img> and <br> have no close tag; following nodes must stay siblings.
    root = parse('<div id="d"><img src="p.jpg">one<br>two</div>')
    d = root.find(by_id("d"))
    assert d.text() == "one two"
    assert d.find(lambda n: n.tag == "img").get("src") == "p.jpg"


def test_mismatched_close_tag_is_ignored():
    # Stray </p> must not corrupt the tree; #keep stays reachable.
    root = parse('<div><p>a</span></p></div><div id="keep">ok</div>')
    assert root.find(by_id("keep")).text() == "ok"


def test_find_by_hook_and_class_and_prefix():
    root = parse('<i data-hook="review-star-rating" class="a-star-5">'
                 '<span class="a-icon-alt">5.0 out of 5 stars</span></i>'
                 '<div id="corePrice_x">y</div>')
    assert root.find(by_hook("review-star-rating")).text() == "5.0 out of 5 stars"
    assert root.find(by_class("a-icon-alt")) is not None
    assert root.find(by_id_prefix("corePrice")).id == "corePrice_x"


def test_entities_are_unescaped():
    root = parse('<p id="p">Faber &amp; Co &#8377;100</p>')
    assert root.find(by_id("p")).text() == "Faber & Co ₹100"
