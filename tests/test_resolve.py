"""`resolve`: name/path lookup and the `public` sheet flag."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sheetv2 as sv2  # noqa: E402
from resolve import canister_is_public, find_row, parse_query  # noqa: E402

ROWS = [
    {"name": "fleet-file-registry", "canister_id": "aaaaa-aa", "section": "Product", "stand": "fleet-registry"},
    {"name": "alpha-backend", "canister_id": "bbbbb-bb", "section": "Deployments", "stand": "alpha"},
    {"name": "pending", "canister_id": "", "section": "Product", "stand": "x"},
]

SHEET = {
    "sections": [
        {"name": "Product", "stands": [{"name": "fleet-registry", "canisters": [
            {"name": "fleet-file-registry", "kind": "backend", "wasm": "file-registry", "controllers": ["$self"],
             "public": True},
        ]}]},
        {"name": "Deployments", "arrangements": {"stand_template": {
            "name_pattern": "*", "created_by": "$self",
            "canisters": [{"name": "{stand}-backend", "kind": "backend", "wasm": "realm-backend",
                           "controllers": ["$self"]}],
        }}, "stands": []},
    ],
}


def test_parse_name_and_path():
    assert parse_query({"name": "x"}) == ("", "", "x")
    assert parse_query({"path": "A/b/c"}) == ("A", "b", "c")
    for bad in ({}, {"path": "a/b"}, {"path": "a//c"}, {"path": "a/b/c", "name": "d"}):
        with pytest.raises(ValueError):
            parse_query(bad)


def test_find_row_by_name_and_path():
    assert find_row(ROWS, "", "", "alpha-backend")["canister_id"] == "bbbbb-bb"
    assert find_row(ROWS, "Deployments", "alpha", "alpha-backend") is not None
    assert find_row(ROWS, "Product", "alpha", "alpha-backend") is None
    assert find_row(ROWS, "", "", "pending") is None


def test_public_flag_on_declared_and_template_members():
    assert canister_is_public(SHEET, "fleet-file-registry", "Product", "fleet-registry") is True
    assert canister_is_public(SHEET, "alpha-backend", "Deployments", "alpha") is False
    tmpl = SHEET["sections"][1]["arrangements"]["stand_template"]["canisters"][0]
    tmpl["public"] = True
    try:
        assert canister_is_public(SHEET, "alpha-backend", "Deployments", "alpha") is True
        assert canister_is_public(SHEET, "alpha-backend", "Product", "alpha") is False
    finally:
        del tmpl["public"]


def test_public_must_be_boolean():
    errors: list[str] = []
    sv2._validate_canister({"kind": "backend", "wasm": "x", "controllers": ["$self"], "public": "yes"},
                           "c", errors, in_sections=False)
    assert any("public must be a boolean" in e for e in errors)
