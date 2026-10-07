"""The short `casals up` summary and the log file it points at."""

from datetime import datetime

from casals_cli.runlog import display_path, log_file_path
from casals_cli.up import _tc, _upload_note, frontend_url


def test_display_path_uses_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = str(tmp_path / "casals" / "logs" / "minimal-local.log")
    assert display_path(path) == "~/casals/logs/minimal-local.log"


def test_log_file_names_the_sheet_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASALS_HOME", str(tmp_path))
    path = log_file_path("minimal", "local", now=datetime(2026, 10, 6, 18, 30, 5))
    assert path.endswith("/logs/minimal-local-20261006-183005.log")


def test_frontend_url_on_a_local_replica_and_on_mainnet():
    assert frontend_url("http://127.0.0.1:8000", "aaaaa-aa", mainnet=False) == "http://aaaaa-aa.localhost:8000/"
    assert frontend_url("https://icp0.io", "aaaaa-aa", mainnet=True) == "https://aaaaa-aa.icp0.io/"


def test_upload_note_names_wasms_and_counts_ui_files():
    rows = [
        {"kind": "wasm", "family": "casals-backend", "bytes": 6_400_000},
        {"kind": "wasm", "family": "hello-world-rust", "bytes": 300_000},
        {"kind": "bundle", "action": "uploaded"},
        {"kind": "bundle", "action": "deleted"},
    ]
    mb = f"{(6_400_000 + 300_000) / 1_048_576:.1f}"
    assert _upload_note(rows) == f"casals-backend, hello-world-rust — {mb} MB; UI, 1 files"


def test_tc_rounds_to_a_whole_number_when_it_is_close():
    assert _tc(1000.0) == "1000"
    assert _tc(100.60) == "100.6"
    assert _tc(98.53) == "98.5"
    assert _tc(1.2) == "1.2"
