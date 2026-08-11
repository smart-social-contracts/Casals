"""Tests for scripts/resolve_registry_id.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import resolve_registry_id as resolve  # noqa: E402


def test_id_from_mappings_prefers_ic_data(tmp_path, monkeypatch):
    data_dir = tmp_path / ".icp" / "data" / "mappings"
    data_dir.mkdir(parents=True)
    (data_dir / "ic.ids.json").write_text(
        json.dumps({"ic_file_registry": "iby3p-tiaaa-aaaao-bbapq-cai"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(resolve, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(resolve, "_id_from_icp", lambda _env: "")
    assert resolve.resolve_registry_id() == "iby3p-tiaaa-aaaao-bbapq-cai"


def test_id_from_icp_beats_mappings(tmp_path, monkeypatch):
    data_dir = tmp_path / ".icp" / "data" / "mappings"
    data_dir.mkdir(parents=True)
    (data_dir / "ic.ids.json").write_text(
        json.dumps({"ic_file_registry": "from-file"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(resolve, "REPO_ROOT", tmp_path)

    def fake_icp(env: str) -> str:
        return "from-icp" if env == "ic" else ""

    monkeypatch.setattr(resolve, "_id_from_icp", fake_icp)
    assert resolve.resolve_registry_id() == "from-icp"


def test_resolve_registry_id_empty_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(resolve, "_id_from_icp", lambda _env: "")
    assert resolve.resolve_registry_id() == ""
