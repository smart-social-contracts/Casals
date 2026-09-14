"""Unit tests for casals CLI utilities — no IC runtime required."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from casals_cli.main import _build_parser  # noqa: E402
from casals_cli.util import (  # noqa: E402
    candid_text_arg,
    candid_unescape,
    load_json_file,
    parse_icp_output,
)

CLI_SCRIPT = os.path.join(REPO_ROOT, "scripts", "casals.py")


def _encode_candid(data: dict) -> str:
    inner = json.dumps(data).replace("\\", "\\\\").replace('"', '\\"')
    return f'("{inner}")'


def _fake_proc(stdout="", returncode=0, stderr=""):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


class TestParse:
    def test_candid_text_dict(self):
        assert parse_icp_output(_encode_candid({"ok": True})) == {"ok": True}

    def test_multiline_candid(self):
        raw = '(\n  "' + json.dumps({"ok": True}).replace('"', '\\"') + '"\n)'
        assert parse_icp_output(raw) == {"ok": True}


class TestCandidUnescape:
    def test_newline(self):
        assert candid_unescape("a\\nb") == "a\nb"


class TestCandidTextArg:
    def test_wraps(self):
        assert candid_text_arg("{}").startswith('("')


class TestParserV2:
    @pytest.fixture
    def parser(self):
        return _build_parser()

    @pytest.mark.parametrize("cmd", ["status", "tree", "plan", "verify", "export"])
    def test_simple_commands(self, parser, cmd):
        assert parser.parse_args([cmd]).command == cmd

    def test_register(self, parser):
        args = parser.parse_args([
            "register", "Hello", "hello-backend", "aaaaa-aa", "backend",
        ])
        assert args.command == "register" and args.kind == "backend"


class TestLoadJson:
    def test_valid(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"name": "x"}')
        assert load_json_file(str(p))["name"] == "x"


class TestSubprocessRouting:
    @pytest.fixture(scope="class")
    def fake_icp_dir(self, tmp_path_factory):
        d = tmp_path_factory.mktemp("fake_icp")
        script = d / "icp"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            'inner = json.dumps({"ok": True, "version": "0.2.0"})\n'
            'print(f"(\\"{inner.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}\\")")\n'
        )
        script.chmod(0o755)
        return d

    def _run(self, argv, icp_dir):
        env = os.environ.copy()
        env["PATH"] = str(icp_dir) + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            [sys.executable, CLI_SCRIPT] + argv,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_status_exits_0(self, fake_icp_dir):
        # status needs bindings or --conductor; pass conductor override
        result = self._run(["--conductor", "aaaaa-aa", "status"], fake_icp_dir)
        assert result.returncode == 0
