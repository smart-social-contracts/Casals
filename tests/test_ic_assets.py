"""`.ic-assets.json5` parsing and per-asset property resolution (dfx semantics)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ic_assets  # noqa: E402

POLICY = """
// comment
[
  { match: "**/*", "security_policy": "standard",
    headers: { "Content-Security-Policy": "default-src 'self';", }, },
  { "match": "assets/**", "headers": { "Cache-Control": "immutable" } },
  { 'match': "**/*.{html,json}", cache: { max_age: 0 }, "allow_raw_access": true },
]
"""


def test_parse_json5_subset():
    rules = ic_assets.rules_from(POLICY)
    assert [r["match"] for r in rules] == ["**/*", "assets/**", "**/*.{html,json}"]
    assert rules[2]["cache"] == {"max_age": 0}


def test_glob_semantics():
    m = ic_assets.glob_match
    assert m("**/*", "index.html") and m("**/*", "a/b/c.js")
    assert m("*.js", "b.js") and not m("*.js", "a/b.js")
    assert m("**/*.{html,json}", "a/b.html") and not m("**/*.{html,json}", "a/b.js")
    assert m(".well-known/*", ".well-known/x") and not m(".well-known/*", ".well-known/x/y")
    assert m("assets/**", "assets/app.css") and not m("assets/**", "asset/app.css")
    assert m("version", "version") and not m("version", "versions")
    assert m("[a-c].txt", "b.txt") and not m("[a-c].txt", "d.txt")


def test_properties_later_rules_win_per_header():
    rules = ic_assets.rules_from(POLICY)
    css = ic_assets.properties_for("/assets/app.css", rules)
    assert css["headers"]["Content-Security-Policy"] == "default-src 'self';"
    assert css["headers"]["X-Frame-Options"] == "DENY"  # from security_policy: standard
    assert css["headers"]["Cache-Control"] == "immutable"
    assert css["max_age"] is None and css["allow_raw_access"] is None
    html = ic_assets.properties_for("/index.html", rules)
    assert "Cache-Control" not in html["headers"]
    assert html["max_age"] == 0 and html["allow_raw_access"] is True


def test_no_rule_means_no_properties():
    props = ic_assets.properties_for("/x.bin", ic_assets.rules_from('[{"match": "*.js", "headers": {"A": "b"}}]'))
    assert props["headers"] is None and not ic_assets.any_properties(props)
