"""Unit tests for casals CLI utilities — no IC runtime required."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from casals_cli.main import _build_parser  # noqa: E402
from casals_cli.util import (  # noqa: E402
    candid_text_arg,
    candid_unescape,
    load_json_file,
    missing_icp_cli_message,
    parse_icp_output,
    run_icp_cmd,
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

    @pytest.mark.parametrize("cmd", ["status", "tree", "plan", "export"])
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


class TestMissingIcpCli:
    def test_message_points_at_the_repo_not_a_command(self):
        text = missing_icp_cli_message()
        assert text == "casals needs icp-cli on PATH. See https://github.com/dfinity/icp-cli"
        assert "npm" not in text

    def test_run_icp_cmd_rewrites_file_not_found(self, monkeypatch):
        def _missing(*_a, **_k):
            raise FileNotFoundError(2, "No such file or directory", "icp")

        monkeypatch.setattr("casals_cli.util.subprocess.run", _missing)
        with pytest.raises(RuntimeError, match="github.com/dfinity/icp-cli") as caught:
            run_icp_cmd(["icp", "identity", "principal"])
        assert "No such file or directory" not in str(caught.value)
        assert "npm" not in str(caught.value)


def _fake_wheel(path, files: dict[str, bytes]) -> str:
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ic_casals-0.0.0.dist-info/METADATA", "")
        for name, data in files.items():
            z.writestr(name, data)
    return str(path)


def test_check_wheel_wants_exact_copies_of_everything_the_cli_imports(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_wheel", os.path.join(REPO_ROOT, "scripts", "check_wheel.py"))
    cw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cw)
    src = {n: open(os.path.join(REPO_ROOT, "src", f"{n}.py"), "rb").read()
           for n in ("sheetv2", "access_code", "auth", "ic_assets", "commanders")}
    shared = {f"casals_cli/_shared/{n}.py": data for n, data in src.items()}
    cli = {"casals_cli/oracle.py": b"from sheetv2 import validate\n"}

    assert cw.check(_fake_wheel(tmp_path / "ok.whl", {**cli, **shared})) == []

    no_commanders = {k: v for k, v in shared.items() if "commanders" not in k}
    assert cw.check(_fake_wheel(tmp_path / "a.whl", {**cli, **no_commanders})) == [
        "imported from src/ but not in casals_cli/_shared/: commanders"]  # sheetv2 imports it lazily

    stale = {**shared, "casals_cli/_shared/auth.py": b"# old\n"}
    assert cw.check(_fake_wheel(tmp_path / "b.whl", {**cli, **stale})) == [
        "casals_cli/_shared/auth.py differs from src/auth.py"]

    assert cw.check(_fake_wheel(tmp_path / "c.whl", {**cli, **shared, "sheetv2.py": src["sheetv2"]})) == [
        "installed outside casals_cli/: sheetv2.py"]


class TestOrchestraRefs:
    """`$orchestra:<sheet>/<canister>` under environments.<env> becomes that
    canister's id in the other orchestra's same environment."""

    URL = "https://icp-api.io"

    def _save(self, tmp_path, monkeypatch, sheet_name="realms-product", env="staging", url=URL):
        from casals_cli.bindings import Bindings

        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        Bindings(sheet_name=sheet_name, env=env, network_url=url, deployer="2vxsx-fae",
                 conductor={"casals-backend": "lcbqk-5qaaa-aaaai-ravda-cai"},
                 backend_id="lcbqk-5qaaa-aaaai-ravda-cai").save()

    def _ic(self, bindings):
        ic = MagicMock(network_url=self.URL)
        ic.query.return_value = {"bindings": bindings}
        return ic

    def _sheet(self):
        return {"name": "gaas", "environments": {
            "staging": {"realms_product": {"marketplace_id": "$orchestra:realms-product/marketplace-backend"}},
            "production": {"realms_product": {"marketplace_id": "$orchestra:realms-product/marketplace-backend"}},
        }}

    def test_resolves_only_the_target_environment(self, tmp_path, monkeypatch):
        from casals_cli.bindings import resolve_orchestra_refs

        self._save(tmp_path, monkeypatch)
        ic = self._ic({"marketplace-backend": "mkt7a-aaaaa-aaaai-ravdq-cai"})
        sheet = self._sheet()
        out = resolve_orchestra_refs(ic, sheet, "staging")
        assert out["environments"]["staging"]["realms_product"]["marketplace_id"] == "mkt7a-aaaaa-aaaai-ravdq-cai"
        assert out["environments"]["production"] == sheet["environments"]["production"]
        assert sheet["environments"]["staging"]["realms_product"]["marketplace_id"].startswith("$orchestra:")
        ic.query.assert_called_once_with("lcbqk-5qaaa-aaaai-ravda-cai", "get_bindings")

    def test_missing_orchestra_says_deploy_it_first(self, tmp_path, monkeypatch):
        from casals_cli.bindings import resolve_orchestra_refs

        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="deploy realms-product staging first"):
            resolve_orchestra_refs(self._ic({}), self._sheet(), "staging")

    def test_missing_canister(self, tmp_path, monkeypatch):
        from casals_cli.bindings import resolve_orchestra_refs

        self._save(tmp_path, monkeypatch)
        with pytest.raises(RuntimeError, match="has no canister 'marketplace-backend'"):
            resolve_orchestra_refs(self._ic({"token-backend": "aaaaa-aa"}), self._sheet(), "staging")

    def test_bindings_from_another_network(self, tmp_path, monkeypatch):
        from casals_cli.bindings import resolve_orchestra_refs

        self._save(tmp_path, monkeypatch, url="http://127.0.0.1:8000")
        with pytest.raises(RuntimeError, match="belong to http://127.0.0.1:8000"):
            resolve_orchestra_refs(self._ic({"marketplace-backend": "aaaaa-aa"}), self._sheet(), "staging")

    def test_sheet_without_refs_is_returned_as_is(self):
        from casals_cli.bindings import resolve_orchestra_refs

        sheet = {"name": "x", "environments": {"staging": {"portal_url": "https://staging.gos.earth"}}}
        assert resolve_orchestra_refs(MagicMock(), sheet, "staging") is sheet
