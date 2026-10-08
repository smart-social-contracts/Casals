"""`.ic-assets.json5` parsing and per-asset property resolution (dfx semantics)."""

from __future__ import annotations

import json
import os
import struct
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


def _shipped_ui_policy() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "frontend", "static", ".ic-assets.json5")
    with open(path, encoding="utf-8") as f:
        rules = ic_assets.rules_from(f.read())
    return {key: ic_assets.properties_for(key, rules)["headers"] for key in ("/index.html", "/version")}


def test_shipped_ui_policy_has_no_inline_scripts_or_local_origins():
    headers = _shipped_ui_policy()
    csp = headers["/index.html"]["Content-Security-Policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "localhost" not in csp and "127.0.0.1" not in csp
    assert "clipboard-read=()" in headers["/index.html"]["Permissions-Policy"]


def test_shipped_ui_version_file_is_not_cross_origin():
    assert "Access-Control-Allow-Origin" not in _shipped_ui_policy()["/version"]


def test_shipped_ui_ii_app_metadata():
    """Internet Identity reads the document and its logo cross-origin and drops
    the whole document if one field breaks its limits."""
    static = os.path.join(os.path.dirname(__file__), "..", "frontend", "static")
    with open(os.path.join(static, ".ic-assets.json5"), encoding="utf-8") as f:
        rules = ic_assets.rules_from(f.read())
    assert any(r.get("match") == ".well-known" and r.get("ignore") is False for r in rules)
    doc_headers = ic_assets.properties_for("/.well-known/ii-app-metadata", rules)["headers"]
    assert doc_headers["Content-Type"] == "application/json"
    assert doc_headers["Access-Control-Allow-Origin"] == "*"
    assert ic_assets.properties_for("/logo.png", rules)["headers"]["Access-Control-Allow-Origin"] == "*"

    with open(os.path.join(static, ".well-known", "ii-app-metadata"), "rb") as f:
        raw = f.read()
    assert len(raw) <= 8 * 1024
    doc = json.loads(raw)
    assert 1 <= len(doc["name"]) <= 40
    assert 1 <= len(doc["description"]) <= 120
    assert doc["logo"].startswith("/") and not doc["logo"].startswith("//")
    with open(os.path.join(static, doc["logo"].lstrip("/")), "rb") as f:
        png = f.read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) <= 1024 * 1024
    width, height = struct.unpack(">II", png[16:24])
    assert max(width, height) <= 4096
