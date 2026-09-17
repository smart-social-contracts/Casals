"""Isolated local replicas — no live icp required."""

from __future__ import annotations

import os
import socket

import pytest

from casals_cli.replica import (
    DEFAULT_PORT,
    DEFAULT_URL,
    activate,
    canister_http_url,
    isolating,
    network_url,
    pick_free_port,
    port_from_url,
    replica_home,
    write_replica_project,
)


def test_port_from_url():
    assert port_from_url(None) == DEFAULT_PORT
    assert port_from_url("http://127.0.0.1:8001") == 8001
    assert port_from_url("https://icp0.io") == 443


def test_canister_http_url_uses_env_port(monkeypatch):
    monkeypatch.setenv("CASALS_REPLICA_PORT", "8007")
    monkeypatch.delenv("CASALS_NETWORK_URL", raising=False)
    assert canister_http_url("aaaaa-aa", "/index.html") == "http://aaaaa-aa.localhost:8007/index.html"
    assert canister_http_url("aaaaa-aa", "/", url="http://127.0.0.1:8010") == "http://aaaaa-aa.localhost:8010/"


def test_casals_home_alone_does_not_isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CASALS_HOME", str(tmp_path))
    monkeypatch.delenv("CASALS_REPLICA", raising=False)
    monkeypatch.delenv("CASALS_REPLICA_PORT", raising=False)
    monkeypatch.delenv("CASALS_REPLICA_HOME", raising=False)
    monkeypatch.delenv("CASALS_NETWORK_URL", raising=False)
    assert isolating() is False
    assert replica_home() is None
    assert network_url() == DEFAULT_URL
    r = activate()
    assert r.isolated is False and r.port == DEFAULT_PORT and r.home is None


def test_activate_auto_writes_sidecar_project(monkeypatch, tmp_path):
    monkeypatch.setenv("CASALS_HOME", str(tmp_path))
    monkeypatch.setenv("CASALS_REPLICA_PORT", "auto")
    monkeypatch.delenv("CASALS_REPLICA_HOME", raising=False)
    monkeypatch.delenv("CASALS_NETWORK_URL", raising=False)
    r = activate()
    assert r.isolated
    assert r.port >= 8001
    assert r.home == str(tmp_path / ".replica")
    yaml = (tmp_path / ".replica" / "icp.yaml").read_text()
    assert f"port: {r.port}" in yaml
    assert "mode: managed" in yaml
    assert os.environ["CASALS_NETWORK_URL"] == r.url
    assert os.environ["CASALS_REPLICA_HOME"] == r.home


def test_write_replica_project_is_valid_enough(tmp_path):
    path = write_replica_project(str(tmp_path), 8012)
    text = open(path).read()
    assert "port: 8012" in text
    assert (tmp_path / ".icp" / "cache" / "mappings").is_dir()


def test_pick_free_port_is_bindable():
    port = pick_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
