"""Unit tests for scripts/casals.py — no IC runtime or icp-cli required.

Coverage:
  - _parse: Candid text → Python objects (all common output shapes)
  - _candid_unescape: escape sequence handling
  - _candid_text_arg: JSON → Candid text literal encoding
  - _build_parser: all commands, flags, required/optional args
  - _load_sheet_file: valid JSON, missing file, invalid JSON
  - call(): subprocess wiring, env/identity flags, temp-file cleanup, error raises
  - cmd_sheet_deploy: aborts with exit 1 + JSON stderr if set_sheet fails
  - Full subprocess tests with a fake icp binary (pure-Python shim) that verify
    stdout/stderr routing and exit codes for every command.
"""

import json
import os
import subprocess
import sys
import types
from unittest.mock import MagicMock, call as mock_call, patch

import pytest

# Import the canonical CLI module from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import casals_cli as cli  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI_SCRIPT = os.path.join(REPO_ROOT, "scripts", "casals.py")


# ── helpers ─────────────────────────────────────────────────────────────────

def _fake_proc(stdout="", returncode=0, stderr=""):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def _encode_candid(data: dict) -> str:
    """Return a Candid text literal that _parse() will decode to `data`."""
    inner = json.dumps(data).replace("\\", "\\\\").replace('"', '\\"')
    return f'("{inner}")'


def _make_args(env="local", identity=None, **kwargs):
    return types.SimpleNamespace(env=env, identity=identity, **kwargs)


# ── _parse ───────────────────────────────────────────────────────────────────

class TestParse:
    def test_candid_text_dict(self):
        assert cli._parse(_encode_candid({"ok": True, "version": "0.2.0"})) == {
            "ok": True, "version": "0.2.0",
        }

    def test_candid_text_list(self):
        assert cli._parse(_encode_candid([1, 2, 3])) == [1, 2, 3]

    def test_candid_text_empty_dict(self):
        assert cli._parse(_encode_candid({})) == {}

    def test_bare_json_list_no_quotes(self):
        # A bare list has no inner quotes, so falls through to json.loads
        assert cli._parse("[1, 2]") == [1, 2]

    def test_bare_number_falls_through(self):
        assert cli._parse("42") == 42

    def test_non_json_plain_string_returned_as_is(self):
        assert cli._parse("some plain text") == "some plain text"

    def test_multiline_candid_output(self):
        # icp-cli sometimes line-wraps long text returns
        raw = '(\n  "' + json.dumps({"ok": True}).replace('"', '\\"') + '"\n)'
        assert cli._parse(raw) == {"ok": True}

    def test_whitespace_only_input_does_not_crash(self):
        result = cli._parse("   ")
        # Returns something (empty string or raw text) without raising
        assert isinstance(result, (str, dict, list))

    def test_nested_json_survives_round_trip(self):
        data = {"sections": [{"name": "a", "stands": []}], "ok": True}
        assert cli._parse(_encode_candid(data)) == data


# ── _candid_unescape ─────────────────────────────────────────────────────────

class TestCandidUnescape:
    def test_escaped_double_quote(self):
        assert cli._candid_unescape('\\"hello\\"') == '"hello"'

    def test_escaped_newline(self):
        assert cli._candid_unescape("line1\\nline2") == "line1\nline2"

    def test_escaped_tab(self):
        assert cli._candid_unescape("col1\\tcol2") == "col1\tcol2"

    def test_escaped_carriage_return(self):
        assert cli._candid_unescape("a\\rb") == "a\rb"

    def test_escaped_backslash(self):
        assert cli._candid_unescape("a\\\\b") == "a\\b"

    def test_escaped_single_quote(self):
        assert cli._candid_unescape("\\'") == "'"

    def test_no_escapes_passthrough(self):
        assert cli._candid_unescape("hello world 123") == "hello world 123"

    def test_unicode_passthrough(self):
        assert cli._candid_unescape("café") == "café"

    def test_unknown_escape_sequence_left_intact(self):
        # \z is not a known escape; leave the backslash and z intact
        result = cli._candid_unescape("\\z")
        assert "z" in result

    def test_trailing_backslash_not_consumed(self):
        result = cli._candid_unescape("abc\\")
        assert result.endswith("\\")


# ── _candid_text_arg ─────────────────────────────────────────────────────────

class TestCandidTextArg:
    def test_wraps_in_parens_and_quotes(self):
        r = cli._candid_text_arg("{}")
        assert r.startswith('("') and r.endswith('")')

    def test_inner_quotes_are_escaped(self):
        r = cli._candid_text_arg('{"key": "val"}')
        # Outer parens+quotes stripped: remaining must have escaped inner quotes
        inner = r[2:-2]
        assert '\\"' in inner

    def test_inner_backslashes_are_doubled(self):
        r = cli._candid_text_arg('{"path": "a\\\\b"}')
        assert "\\\\\\\\" in r  # original \\ becomes \\\\

    def test_round_trips_through_unescape(self):
        original = '{"ok": true, "sections": [{"name": "x"}]}'
        wrapped = cli._candid_text_arg(original)
        inner = cli._candid_unescape(wrapped[2:-2])
        assert inner == original

    def test_empty_json_object(self):
        r = cli._candid_text_arg("{}")
        assert r == '("{}")'

    def test_empty_string_payload(self):
        r = cli._candid_text_arg("")
        assert r == '("")'


# ── _build_parser ────────────────────────────────────────────────────────────

class TestParser:
    @pytest.fixture(autouse=True)
    def parser(self):
        self._p = cli._build_parser()

    def _parse(self, argv):
        return self._p.parse_args(argv)

    # defaults
    def test_default_env_is_local(self):
        assert self._parse(["status"]).env == "local"

    def test_default_identity_is_none(self):
        assert self._parse(["status"]).identity is None

    # env flag
    def test_env_short_flag(self):
        assert self._parse(["-e", "ic", "status"]).env == "ic"

    def test_env_long_flag(self):
        assert self._parse(["--env", "ic", "status"]).env == "ic"

    # identity flag
    def test_identity_flag(self):
        assert self._parse(["--identity", "casals", "status"]).identity == "casals"

    # simple commands
    @pytest.mark.parametrize("cmd", ["status", "tree", "events", "wasms", "cycles", "pool"])
    def test_simple_command_parsed(self, cmd):
        args = self._parse([cmd])
        assert args.command == cmd

    # sheet subcommands
    def test_sheet_get(self):
        args = self._parse(["sheet", "get"])
        assert args.command == "sheet" and args.sheet_command == "get"

    def test_sheet_set_with_file(self):
        args = self._parse(["sheet", "set", "/tmp/my.json"])
        assert args.sheet_command == "set" and args.file == "/tmp/my.json"

    def test_sheet_set_requires_file(self):
        with pytest.raises(SystemExit):
            self._parse(["sheet", "set"])

    def test_sheet_deploy_no_file(self):
        args = self._parse(["sheet", "deploy"])
        assert args.sheet_command == "deploy" and args.file is None

    def test_sheet_deploy_with_file(self):
        args = self._parse(["sheet", "deploy", "/tmp/my.json"])
        assert args.sheet_command == "deploy" and args.file == "/tmp/my.json"

    # error cases
    def test_no_command_exits(self):
        with pytest.raises(SystemExit):
            self._parse([])

    def test_unknown_command_exits(self):
        with pytest.raises(SystemExit):
            self._parse(["nonexistent"])

    def test_flags_before_command(self):
        args = self._parse(["-e", "ic", "--identity", "me", "status"])
        assert args.env == "ic" and args.identity == "me"


# ── _load_sheet_file ─────────────────────────────────────────────────────────

class TestLoadSheetFile:
    def test_valid_json_file(self, tmp_path):
        sheet = {"sections": [], "name": "demo", "description": "test"}
        f = tmp_path / "sheet.json"
        f.write_text(json.dumps(sheet))
        assert cli._load_sheet_file(str(f)) == sheet

    def test_nested_json_preserved(self, tmp_path):
        sheet = {"sections": [{"name": "s", "stands": [{"name": "d"}]}]}
        f = tmp_path / "sheet.json"
        f.write_text(json.dumps(sheet))
        assert cli._load_sheet_file(str(f)) == sheet

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cli._load_sheet_file(str(tmp_path / "nope.json"))

    def test_invalid_json_raises_json_decode_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json {{{")
        with pytest.raises(json.JSONDecodeError):
            cli._load_sheet_file(str(f))

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            cli._load_sheet_file(str(f))


# ── call() — subprocess wiring ───────────────────────────────────────────────

class TestCall:
    @patch("subprocess.run")
    def test_invokes_icp_canister_call(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        result = cli.call("casals_backend", "get_status", _make_args(), "{}")
        assert result == {"ok": True}
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "icp"
        assert "canister" in cmd
        assert "call" in cmd
        assert "casals_backend" in cmd
        assert "get_status" in cmd

    @patch("subprocess.run")
    def test_env_flag_appended(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        cli.call("casals_backend", "get_status", _make_args(env="ic"), "{}")
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("-e")
        assert cmd[idx + 1] == "ic"

    @patch("subprocess.run")
    def test_identity_flag_appended_when_set(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        cli.call("casals_backend", "get_status", _make_args(identity="casals"), "{}")
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("--identity")
        assert cmd[idx + 1] == "casals"

    @patch("subprocess.run")
    def test_identity_flag_absent_when_none(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        cli.call("casals_backend", "get_status", _make_args(), "{}")
        cmd = mock_run.call_args[0][0]
        assert "--identity" not in cmd

    @patch("subprocess.run")
    def test_uses_args_file_for_payload(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        cli.call("casals_backend", "set_sheet", _make_args(), '{"name": "demo"}')
        cmd = mock_run.call_args[0][0]
        assert "--args-file" in cmd
        assert "--args-format" in cmd
        assert cmd[cmd.index("--args-format") + 1] == "candid"

    @patch("subprocess.run")
    def test_payload_written_to_temp_file_as_candid(self, mock_run):
        captured = {}

        def side_effect(cmd, **kwargs):
            idx = cmd.index("--args-file")
            path = cmd[idx + 1]
            with open(path) as f:
                captured["content"] = f.read()
            return _fake_proc(stdout=_encode_candid({"ok": True}))

        mock_run.side_effect = side_effect
        cli.call("casals_backend", "set_sheet", _make_args(), '{"name": "x"}')
        # Content must be a Candid text literal: ("...")
        assert captured["content"].startswith('("')
        assert captured["content"].endswith('")')
        inner = cli._candid_unescape(captured["content"][2:-2])
        assert json.loads(inner) == {"name": "x"}

    @patch("subprocess.run")
    def test_temp_file_deleted_after_call(self, mock_run):
        created_paths = []

        def side_effect(cmd, **kwargs):
            idx = cmd.index("--args-file")
            created_paths.append(cmd[idx + 1])
            return _fake_proc(stdout=_encode_candid({"ok": True}))

        mock_run.side_effect = side_effect
        cli.call("casals_backend", "get_status", _make_args(), "{}")
        assert created_paths, "no --args-file arg found"
        assert not os.path.exists(created_paths[0]), "temp file not cleaned up"

    @patch("subprocess.run")
    def test_temp_file_deleted_even_on_icp_failure(self, mock_run):
        created_paths = []

        def side_effect(cmd, **kwargs):
            idx = cmd.index("--args-file")
            created_paths.append(cmd[idx + 1])
            return _fake_proc(returncode=1, stderr="broken")

        mock_run.side_effect = side_effect
        with pytest.raises(RuntimeError):
            cli.call("casals_backend", "get_status", _make_args(), "{}")
        assert created_paths and not os.path.exists(created_paths[0])

    @patch("subprocess.run")
    def test_nonzero_exit_raises_runtime_error(self, mock_run):
        mock_run.return_value = _fake_proc(returncode=1, stderr="network error")
        with pytest.raises(RuntimeError, match="failed"):
            cli.call("casals_backend", "get_status", _make_args(), "{}")

    @patch("subprocess.run")
    def test_large_json_payload(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        big = json.dumps({"data": "x" * 500_000})
        result = cli.call("casals_backend", "set_sheet", _make_args(), big)
        assert result == {"ok": True}


# ── cmd_sheet_deploy — set_sheet failure aborts ──────────────────────────────

class TestSheetDeployAbortOnSetFailure:
    @patch("subprocess.run")
    def test_aborts_with_exit_1_if_set_sheet_fails(self, mock_run, tmp_path):
        mock_run.return_value = _fake_proc(
            stdout=_encode_candid({"ok": False, "error": "forbidden"})
        )
        sheet_file = tmp_path / "sheet.json"
        sheet_file.write_text(json.dumps({"sections": []}))
        args = _make_args(file=str(sheet_file))
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_sheet_deploy(args)
        assert exc_info.value.code == 1

    @patch("subprocess.run")
    def test_deploy_sheet_not_called_after_set_sheet_failure(self, mock_run, tmp_path):
        mock_run.return_value = _fake_proc(
            stdout=_encode_candid({"ok": False, "error": "forbidden"})
        )
        sheet_file = tmp_path / "sheet.json"
        sheet_file.write_text(json.dumps({"sections": []}))
        args = _make_args(file=str(sheet_file))
        with pytest.raises(SystemExit):
            cli.cmd_sheet_deploy(args)
        # set_sheet called once, deploy_sheet never called
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_no_file_skips_set_sheet(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_encode_candid({"ok": True}))
        args = _make_args(file=None)
        cli.cmd_sheet_deploy(args)
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "deploy_sheet" in cmd


# ── subprocess tests with a fake icp binary ──────────────────────────────────

@pytest.fixture(scope="module")
def fake_icp_dir(tmp_path_factory):
    """A directory containing a fake `icp` script (Python) that always returns
    a successful Candid JSON response, placed first on PATH."""
    d = tmp_path_factory.mktemp("fake_icp")
    script = d / "icp"
    # Use Python so we don't have to worry about shell escaping of JSON.
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "data = {'ok': True, 'version': '0.2.0', 'sections': []}\n"
        "inner = json.dumps(data).replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"')\n"
        f"print(f'(\"{{inner}}\")')\n"
    )
    script.chmod(0o755)
    return d


@pytest.fixture(scope="module")
def failing_icp_dir(tmp_path_factory):
    """A fake `icp` that always exits with code 2."""
    d = tmp_path_factory.mktemp("fail_icp")
    script = d / "icp"
    script.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n")
    script.chmod(0o755)
    return d


def _run_cli(argv, icp_dir):
    env = os.environ.copy()
    env["PATH"] = str(icp_dir) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [sys.executable, CLI_SCRIPT] + argv,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class TestSubprocessRouting:
    """Verify stdout/stderr routing and exit codes using the fake icp."""

    @pytest.mark.parametrize("cmd", [
        ["status"],
        ["tree"],
        ["events"],
        ["wasms"],
        ["cycles"],
        ["pool"],
        ["sheet", "get"],
    ])
    def test_read_command_exits_0_and_stdout_is_json(self, fake_icp_dir, cmd):
        result = _run_cli(cmd, fake_icp_dir)
        assert result.returncode == 0, f"{cmd}: stderr={result.stderr!r}"
        assert result.stderr == "", f"{cmd}: unexpected stderr: {result.stderr!r}"
        data = json.loads(result.stdout)
        assert isinstance(data, dict)

    def test_sheet_set_exits_0(self, fake_icp_dir, tmp_path):
        f = tmp_path / "s.json"
        f.write_text(json.dumps({"sections": []}))
        result = _run_cli(["sheet", "set", str(f)], fake_icp_dir)
        assert result.returncode == 0
        json.loads(result.stdout)

    def test_sheet_deploy_no_file_exits_0(self, fake_icp_dir):
        result = _run_cli(["sheet", "deploy"], fake_icp_dir)
        assert result.returncode == 0
        json.loads(result.stdout)

    def test_sheet_deploy_with_file_exits_0(self, fake_icp_dir, tmp_path):
        f = tmp_path / "s.json"
        f.write_text(json.dumps({"sections": []}))
        result = _run_cli(["sheet", "deploy", str(f)], fake_icp_dir)
        assert result.returncode == 0
        json.loads(result.stdout)

    def test_icp_failure_exits_1(self, failing_icp_dir):
        result = _run_cli(["status"], failing_icp_dir)
        assert result.returncode == 1

    def test_icp_failure_stderr_is_json_with_ok_false(self, failing_icp_dir):
        result = _run_cli(["status"], failing_icp_dir)
        err = json.loads(result.stderr)
        assert err.get("ok") is False
        assert "error" in err

    def test_icp_failure_stdout_is_empty(self, failing_icp_dir):
        result = _run_cli(["status"], failing_icp_dir)
        assert result.stdout.strip() == ""

    def test_no_command_exits_nonzero(self, fake_icp_dir):
        result = _run_cli([], fake_icp_dir)
        assert result.returncode != 0

    def test_unknown_command_exits_nonzero(self, fake_icp_dir):
        result = _run_cli(["badcmd"], fake_icp_dir)
        assert result.returncode != 0

    def test_sheet_set_missing_file_exits_1_json_stderr(self, fake_icp_dir):
        result = _run_cli(["sheet", "set", "/nonexistent/sheet.json"], fake_icp_dir)
        assert result.returncode == 1
        err = json.loads(result.stderr)
        assert err.get("ok") is False

    def test_sheet_set_invalid_json_exits_1_json_stderr(self, fake_icp_dir, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        result = _run_cli(["sheet", "set", str(f)], fake_icp_dir)
        assert result.returncode == 1
        err = json.loads(result.stderr)
        assert err.get("ok") is False

    def test_env_flag_accepted(self, fake_icp_dir):
        result = _run_cli(["-e", "ic", "status"], fake_icp_dir)
        assert result.returncode == 0

    def test_identity_flag_accepted(self, fake_icp_dir):
        result = _run_cli(["--identity", "casals", "status"], fake_icp_dir)
        assert result.returncode == 0

    def test_output_is_pretty_printed(self, fake_icp_dir):
        result = _run_cli(["status"], fake_icp_dir)
        assert result.returncode == 0
        # Pretty-printed JSON has newlines
        assert "\n" in result.stdout
