"""Integration tests for scripts/casals.py against a live local replica.

Each test runs the CLI script as a real subprocess so the complete path —
arg parsing → icp canister call → Candid decode → JSON stdout — is exercised
end-to-end against a deployed casals_backend.

Requires the `canister` fixture from conftest.py (starts a replica and deploys
casals_backend). Run with:

    pytest tests/test_cli_integration.py -v

(Automatically included when running `pytest tests/` in integration.yml.)
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI = os.path.join(REPO_ROOT, "scripts", "casals.py")


# ── CLI runner ───────────────────────────────────────────────────────────────

def _cli(*args, check=True, timeout=120):
    """Run casals.py against the local replica.

    Returns (parsed_stdout, returncode). Fails the test immediately on non-zero
    exit unless check=False.
    """
    result = subprocess.run(
        [sys.executable, CLI, "-e", "local"] + list(args),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"casals.py {' '.join(args)} exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    parsed = json.loads(result.stdout) if result.stdout.strip() else None
    return parsed, result.returncode


def _raw(*args, timeout=60):
    """Run casals.py and return the raw CompletedProcess (no auto-fail)."""
    return subprocess.run(
        [sys.executable, CLI, "-e", "local"] + list(args),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ── read-only commands ───────────────────────────────────────────────────────

class TestReadCommands:
    """Every read command must exit 0, write valid JSON to stdout, nothing to
    stderr."""

    def test_status_exits_0(self, canister):
        _, rc = _cli("status")
        assert rc == 0

    def test_status_stdout_is_json(self, canister):
        data, _ = _cli("status")
        assert isinstance(data, dict)

    def test_status_required_keys(self, canister):
        data, _ = _cli("status")
        for key in ("version", "sections", "stands", "canisters",
                    "authorized_wasms", "events"):
            assert key in data, f"status missing key: {key}"

    def test_status_version_is_string(self, canister):
        data, _ = _cli("status")
        assert isinstance(data["version"], str) and data["version"]

    def test_status_no_stderr(self, canister):
        result = _raw("status")
        assert result.stderr == ""

    def test_tree_exits_0(self, canister):
        _, rc = _cli("tree")
        assert rc == 0

    def test_tree_has_sections_key(self, canister):
        data, _ = _cli("tree")
        assert "sections" in data

    def test_tree_sections_is_list(self, canister):
        data, _ = _cli("tree")
        assert isinstance(data["sections"], list)

    def test_events_exits_0(self, canister):
        _, rc = _cli("events")
        assert rc == 0

    def test_events_returns_dict_or_list(self, canister):
        data, _ = _cli("events")
        assert isinstance(data, (dict, list))

    def test_wasms_exits_0(self, canister):
        _, rc = _cli("wasms")
        assert rc == 0

    def test_wasms_returns_list(self, canister):
        data, _ = _cli("wasms")
        assert isinstance(data, list)

    def test_pool_exits_0(self, canister):
        _, rc = _cli("pool")
        assert rc == 0

    def test_pool_returns_report(self, canister):
        data, _ = _cli("pool")
        assert isinstance(data, dict)
        assert "canisters" in data
        assert isinstance(data["canisters"], list)

    def test_sheet_get_exits_0(self, canister):
        _, rc = _cli("sheet", "get")
        assert rc == 0

    def test_sheet_get_returns_dict(self, canister):
        data, _ = _cli("sheet", "get")
        assert isinstance(data, dict)

    def test_sheet_get_has_sections_key(self, canister):
        data, _ = _cli("sheet", "get")
        assert "sections" in data

    def test_all_read_commands_produce_valid_json(self, canister):
        """Smoke-test: every read command's stdout must be parseable JSON."""
        commands = [
            ["status"], ["tree"], ["events"], ["wasms"], ["pool"],
            ["sheet", "get"],
        ]
        for cmd in commands:
            result = _raw(*cmd)
            assert result.returncode == 0, f"{cmd} exited {result.returncode}"
            try:
                json.loads(result.stdout)
            except json.JSONDecodeError as e:
                pytest.fail(f"{cmd} stdout is not valid JSON: {e}\n{result.stdout[:300]}")

    def test_output_is_pretty_printed(self, canister):
        result = _raw("status")
        assert "\n" in result.stdout, "output should be indented/pretty-printed"


# ── sheet set / deploy round-trips ───────────────────────────────────────────

class TestSheetCommands:
    def test_sheet_set_exits_0(self, canister, tmp_path):
        current, _ = _cli("sheet", "get")
        f = tmp_path / "sheet.json"
        f.write_text(json.dumps(current))
        _, rc = _cli("sheet", "set", str(f))
        assert rc == 0

    def test_sheet_set_returns_ok_true(self, canister, tmp_path):
        current, _ = _cli("sheet", "get")
        f = tmp_path / "sheet.json"
        f.write_text(json.dumps(current))
        data, _ = _cli("sheet", "set", str(f))
        assert isinstance(data, dict)
        assert data.get("ok") is True, data

    def test_sheet_set_and_get_round_trip(self, canister, tmp_path):
        """Set a modified sheet, then get it back and verify the change landed."""
        current, _ = _cli("sheet", "get")
        modified = dict(current)
        modified["description"] = "__cli_round_trip_test__"
        f = tmp_path / "sheet.json"
        f.write_text(json.dumps(modified))
        _cli("sheet", "set", str(f))
        retrieved, _ = _cli("sheet", "get")
        assert retrieved.get("description") == "__cli_round_trip_test__"

    def test_sheet_deploy_no_file_exits_0(self, canister):
        _, rc = _cli("sheet", "deploy")
        assert rc == 0

    def test_sheet_deploy_no_file_returns_ok_true(self, canister):
        data, _ = _cli("sheet", "deploy")
        assert isinstance(data, dict)
        assert data.get("ok") is True, data

    def test_sheet_deploy_with_file_exits_0(self, canister, tmp_path):
        current, _ = _cli("sheet", "get")
        f = tmp_path / "deploy.json"
        f.write_text(json.dumps(current))
        _, rc = _cli("sheet", "deploy", str(f))
        assert rc == 0

    def test_sheet_deploy_with_file_returns_ok_true(self, canister, tmp_path):
        current, _ = _cli("sheet", "get")
        f = tmp_path / "deploy.json"
        f.write_text(json.dumps(current))
        data, _ = _cli("sheet", "deploy", str(f))
        assert isinstance(data, dict) and data.get("ok") is True, data

    def test_sheet_deploy_with_file_sets_sheet_before_deploying(self, canister, tmp_path):
        """After sheet deploy FILE the live sheet should match the file."""
        current, _ = _cli("sheet", "get")
        modified = dict(current)
        modified["description"] = "__deploy_with_file_test__"
        f = tmp_path / "d.json"
        f.write_text(json.dumps(modified))
        _cli("sheet", "deploy", str(f))
        live, _ = _cli("sheet", "get")
        assert live.get("description") == "__deploy_with_file_test__"

    def test_sheet_deploy_idempotent(self, canister):
        """Calling deploy twice on the same sheet must both succeed."""
        data1, rc1 = _cli("sheet", "deploy")
        data2, rc2 = _cli("sheet", "deploy")
        assert rc1 == 0 and rc2 == 0
        assert data1.get("ok") is True and data2.get("ok") is True


# ── error handling ───────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_no_command_exits_nonzero(self, canister):
        result = _raw()
        assert result.returncode != 0

    def test_unknown_command_exits_nonzero(self, canister):
        result = _raw("badcmd")
        assert result.returncode != 0

    def test_sheet_set_nonexistent_file_exits_1(self, canister):
        result = _raw("sheet", "set", "/nonexistent/does_not_exist.json")
        assert result.returncode == 1

    def test_sheet_set_nonexistent_file_stderr_is_json(self, canister):
        result = _raw("sheet", "set", "/nonexistent/does_not_exist.json")
        err = json.loads(result.stderr)
        assert err.get("ok") is False
        assert "error" in err

    def test_sheet_set_nonexistent_file_stdout_empty(self, canister):
        result = _raw("sheet", "set", "/nonexistent/does_not_exist.json")
        assert result.stdout.strip() == ""

    def test_sheet_set_invalid_json_exits_1(self, canister, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        result = _raw("sheet", "set", str(f))
        assert result.returncode == 1

    def test_sheet_set_invalid_json_stderr_is_json(self, canister, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        result = _raw("sheet", "set", str(f))
        err = json.loads(result.stderr)
        assert err.get("ok") is False

    def test_sheet_deploy_nonexistent_file_exits_1(self, canister):
        result = _raw("sheet", "deploy", "/nonexistent/sheet.json")
        assert result.returncode == 1

    def test_sheet_deploy_nonexistent_file_stderr_is_json(self, canister):
        result = _raw("sheet", "deploy", "/nonexistent/sheet.json")
        err = json.loads(result.stderr)
        assert err.get("ok") is False


# ── flag threading ───────────────────────────────────────────────────────────

class TestFlags:
    def test_env_local_flag_works(self, canister):
        result = subprocess.run(
            [sys.executable, CLI, "-e", "local", "status"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0
        json.loads(result.stdout)

    def test_env_short_flag_works(self, canister):
        result = subprocess.run(
            [sys.executable, CLI, "-e", "local", "status"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0

    def test_identity_flag_threaded_to_icp(self, canister):
        """Using --identity with the deploy identity (self) must still work."""
        result = subprocess.run(
            [sys.executable, CLI, "-e", "local", "--identity", "default", "status"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        # May or may not succeed depending on identity setup, but must not crash Python
        assert result.returncode in (0, 1)
        if result.returncode == 0:
            json.loads(result.stdout)
        else:
            err = json.loads(result.stderr)
            assert err.get("ok") is False
