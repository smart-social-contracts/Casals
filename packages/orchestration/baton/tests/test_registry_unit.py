"""Unit tests for registry-backed WASM pull helpers."""

from __future__ import annotations

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from registry import registry_canister_id


class FakeMap:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_registry_canister_id_configured():
    cfg = FakeMap({"file_registry_canister_id": "aaaaa-aa"})
    assert registry_canister_id(cfg) == "aaaaa-aa"


def test_registry_canister_id_missing():
    with pytest.raises(ValueError, match="file_registry_canister_id"):
        registry_canister_id(FakeMap({}))
