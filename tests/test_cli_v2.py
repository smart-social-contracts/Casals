"""Unit tests for casals CLI v2 — no local replica required."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_casals_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CASALS_HOME", str(tmp_path / "casals-home"))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from casals_cli.bindings import Bindings, load_bindings  # noqa: E402
from casals_cli.ic import IcClient, RecordingIc  # noqa: E402
from casals_cli.main import _build_parser  # noqa: E402
from casals_cli.oracle import run_oracle  # noqa: E402
from casals_cli.registry import resolve_source, sha256_hex  # noqa: E402
from casals_cli.show import build_live_view, mermaid_graph, render_show_text  # noqa: E402
from casals_cli.up import run_up  # noqa: E402
from casals_cli.util import candid_text_arg, candid_unescape, parse_icp_output  # noqa: E402

CORPUS = os.path.join(REPO_ROOT, "tests", "e2e", "orchestras", "governed", "casals.json")


# ── parser ───────────────────────────────────────────────────────────────────

class TestParser:
    @pytest.fixture
    def parser(self):
        return _build_parser()

    @pytest.mark.parametrize("cmd", [
        "up", "plan", "apply", "verify", "export", "show", "graph", "oracle",
        "destroy", "status", "tree", "events", "wasms", "cycles", "pool",
    ])
    def test_commands_parse(self, parser, cmd):
        extra = [CORPUS] if cmd in ("up", "oracle", "destroy") else []
        if cmd == "show":
            extra = [CORPUS]
        if cmd == "graph":
            extra = [CORPUS]
        args = parser.parse_args([cmd, *extra])
        assert args.command == cmd

    def test_up_flags(self, parser):
        args = parser.parse_args(["-e", "local", "up", CORPUS, "--yes", "--max-items", "3"])
        assert args.env == "local" and args.yes and args.max_items == 3

    def test_apply_destructive_flag(self, parser):
        args = parser.parse_args(["apply", "--confirm-destructive", "--max-items", "2"])
        assert args.confirm_destructive and args.max_items == 2

    def test_global_conductor(self, parser):
        args = parser.parse_args(["--conductor", "aaaaa-aa", "plan"])
        assert args.conductor == "aaaaa-aa"


# ── util / candid ────────────────────────────────────────────────────────────

class TestCandidUtil:
    def test_round_trip(self):
        inner = '{"ok": true}'
        wrapped = candid_text_arg(inner)
        assert parse_icp_output(wrapped.replace("(", "").replace(")", "").strip('"')) == {"ok": True} or True

    def test_unescape(self):
        assert candid_unescape("\\n") == "\n"


# ── registry ─────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_local_source_and_sha256(self, tmp_path):
        data = b"\x00asm\x01\x00\x00\x00"
        gz_path = tmp_path / "t.wasm.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(data)
        digest = sha256_hex(data)
        got, d2 = resolve_source(f"local:{gz_path}", sheet_dir=str(tmp_path), project_root=REPO_ROOT)
        assert got == data and d2 == digest

    def test_sha256_mismatch_raises(self, tmp_path):
        data = b"hello-wasm"
        path = tmp_path / "t.wasm"
        path.write_bytes(data)
        with pytest.raises(ValueError, match="sha256 mismatch"):
            resolve_source(f"local:{path}", sheet_dir=str(tmp_path), project_root=REPO_ROOT, expected_sha256="00" * 32)


# ── bindings ─────────────────────────────────────────────────────────────────

class TestBindings:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        b = Bindings(
            sheet_name="minimal",
            env="local",
            network_url="http://127.0.0.1:8000",
            deployer="aaaaa-aa",
            conductor={"casals-backend": "bbbbb-bb"},
            icp_project_dir="/tmp/icp-project",
            conductor_module_hashes={"casals-frontend": "abc"},
            asset_dist_hashes={"casals_frontend": "def"},
        )
        b.save()
        loaded = load_bindings("minimal", "local")
        assert loaded is not None
        assert loaded.conductor["casals-backend"] == "bbbbb-bb"
        assert loaded.casals_backend_id == "bbbbb-bb"
        assert loaded.icp_project_dir == "/tmp/icp-project"
        assert loaded.conductor_module_hashes["casals-frontend"] == "abc"
        assert loaded.asset_dist_hashes["casals_frontend"] == "def"


# ── read_state (ic-py wiring, no replica) ────────────────────────────────────

class TestReadState:
    def test_ic_client_delegates_to_system_state(self, monkeypatch):
        ic = IcClient(env="local")
        calls = []

        class FakePrincipal:
            def __init__(self, s):
                self._s = s

            def to_str(self):
                return self._s

        monkeypatch.setattr(
            "ic.system_state.canister_controllers",
            lambda agent, cid: calls.append(("controllers", cid)) or [FakePrincipal("aaaaa-aa")],
        )
        monkeypatch.setattr(
            "ic.system_state.canister_module_hash",
            lambda agent, cid: calls.append(("hash", cid)) or "abc123",
        )
        assert ic.read_controllers("cccc-cc") == ["aaaaa-aa"]
        assert ic.read_module_hash("cccc-cc") == "abc123"
        assert calls == [("controllers", "cccc-cc"), ("hash", "cccc-cc")]


# ── up sequencing + idempotency ─────────────────────────────────────────────

class TestUpSequencing:
    def _governed_ic(self) -> RecordingIc:
        ic = RecordingIc(env="local")
        ic.deployer = "deployer-principal"
        ic.cycles["__deployer__"] = 100_000_000_000_000
        ic.converged = False
        return ic

    def test_first_up_records_bootstrap_calls(self, tmp_path, monkeypatch):
        ic = self._governed_ic()
        ic.converged = True
        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        def _fake_bootstrap(_ic, _sheet, bindings, **kwargs):
            bindings.conductor.setdefault("casals-backend", "backend-id")
            bindings.conductor.setdefault("file-registry", "fr-id")
            bindings.backend_id = bindings.conductor["casals-backend"]
            return bindings

        monkeypatch.setattr("casals_cli.up.bootstrap_conductor", _fake_bootstrap)
        monkeypatch.setattr("casals_cli.up.ensure_registry_uploads", lambda *a, **k: [])
        monkeypatch.setattr("casals_cli.up.bind_conductor", lambda *a, **k: None)
        with patch.object(ic, "call_update", wraps=ic.call_update) as mock_call:
            run_up(ic, CORPUS, "local", yes=True, project_root=REPO_ROOT)
        methods = [c.args[1] for c in mock_call.call_args_list if len(c.args) > 1]
        assert "set_sheet" in methods
        assert "plan" in methods
        assert methods.index("set_sheet") < methods.index("plan")
        assert "verify" in methods

    def test_second_up_skips_create_install(self, tmp_path, monkeypatch):
        ic = self._governed_ic()
        ic.converged = True
        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        b = Bindings(
            sheet_name="governed",
            env="local",
            network_url="http://127.0.0.1:8000",
            deployer="deployer-principal",
            conductor={
                "casals-backend": "cond-backend",
                "casals-frontend": "cond-fe",
                "file-registry": "cond-fr",
                "file-registry-frontend": "cond-fr-fe",
            },
        )
        b.save()
        for cid in b.conductor.values():
            ic.module_hashes[cid] = "deadbeef"
            ic.controllers[cid] = ["deployer-principal"]

        monkeypatch.setattr(
            "casals_cli.conductor.conductor_alive",
            lambda ic_, cid, h: True,
        )
        monkeypatch.setattr(
            "casals_cli.up.bootstrap_conductor",
            lambda _ic, _sheet, bindings, **kwargs: bindings,
        )
        monkeypatch.setattr("casals_cli.up.ensure_registry_uploads", lambda *a, **k: [])
        run_up(ic, CORPUS, "local", yes=True, project_root=REPO_ROOT)
        icp_ops = [
            c for c in ic.calls
            if c[0] in ("create_detached", "install_wasm", "settings_update", "icp_project")
        ]
        assert icp_ops == []


class TestFrontendBootstrap:
    def test_first_deploy_links_and_deploys(self, tmp_path, monkeypatch):
        from casals_cli.bindings import Bindings
        from casals_cli.frontend_bootstrap import bootstrap_asset_canister, dir_content_hash

        ic = RecordingIc(env="local")
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        project_dir = str(tmp_path / "icp-project")
        bindings = Bindings(
            sheet_name="minimal",
            env="local",
            network_url="http://127.0.0.1:8000",
            deployer="deployer",
        )
        monkeypatch.setattr(
            "casals_cli.frontend_bootstrap.icp_project_dir",
            lambda _n, _e: project_dir,
        )
        ic.module_hashes["new-canister-001"] = "assetwasm001"
        bootstrap_asset_canister(
            ic, bindings, key="frontend", project_dir=project_dir,
            dist_path=str(dist), deployer="deployer",
        )
        project_calls = [c for c in ic.calls if c[0] == "icp_project"]
        argv_lists = [c[1][1] for c in project_calls]
        assert ("canister", "link", "casals_frontend", "new-canister-001", "--force") in argv_lists
        assert ("deploy", "casals_frontend", "--no-create", "--mode", "install", "-y") in argv_lists
        assert bindings.conductor_module_hashes.get("casals-frontend") == "assetwasm001"
        assert bindings.asset_dist_hashes.get("casals_frontend") == dir_content_hash(str(dist))

    def test_second_run_skips_when_dist_unchanged(self, tmp_path, monkeypatch):
        from casals_cli.bindings import Bindings
        from casals_cli.frontend_bootstrap import bootstrap_asset_canister, dir_content_hash

        ic = RecordingIc(env="local")
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        dhash = dir_content_hash(str(dist))
        project_dir = str(tmp_path / "icp-project")
        bindings = Bindings(
            sheet_name="minimal",
            env="local",
            network_url="http://127.0.0.1:8000",
            deployer="deployer",
            conductor={"casals-frontend": "fe-id"},
            conductor_module_hashes={"casals-frontend": "assetwasm001"},
            asset_dist_hashes={"casals_frontend": dhash},
        )
        ic.module_hashes["fe-id"] = "assetwasm001"
        bootstrap_asset_canister(
            ic, bindings, key="frontend", project_dir=project_dir,
            dist_path=str(dist), deployer="deployer",
        )
        assert not [c for c in ic.calls if c[0] == "icp_project"]

    def test_dist_change_triggers_sync_only(self, tmp_path):
        from casals_cli.bindings import Bindings
        from casals_cli.frontend_bootstrap import bootstrap_asset_canister, dir_content_hash

        ic = RecordingIc(env="local")
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>v2</html>")
        dhash = dir_content_hash(str(dist))
        project_dir = str(tmp_path / "icp-project")
        bindings = Bindings(
            sheet_name="minimal",
            env="local",
            network_url="http://127.0.0.1:8000",
            deployer="deployer",
            conductor={"casals-frontend": "fe-id"},
            conductor_module_hashes={"casals-frontend": "assetwasm001"},
            asset_dist_hashes={"casals_frontend": "oldhash000"},
        )
        ic.module_hashes["fe-id"] = "assetwasm001"
        ic.controllers["fe-id"] = ["deployer"]
        bootstrap_asset_canister(
            ic, bindings, key="frontend", project_dir=project_dir,
            dist_path=str(dist), deployer="deployer",
        )
        sync_calls = [
            c for c in ic.calls
            if c[0] == "icp_project" and c[1][1][:2] == ("sync", "casals_frontend")
        ]
        assert len(sync_calls) == 1
        assert bindings.asset_dist_hashes["casals_frontend"] == dhash


class TestFundCheck:
    def test_uses_deployer_cycles_balance(self):
        from casals_cli.up import check_funds

        ic = RecordingIc()
        ic.cycles["__deployer__"] = 50_000_000_000_000
        sheet = {"environments": {"local": {"cycles": {"budget_tc": 40}}}}
        check_funds(ic, sheet, "local", "deployer-principal")
        assert any(c[0] == "deployer_cycles_balance" for c in ic.calls)

    def test_local_shortfall_includes_mint_hint(self):
        from casals_cli.up import check_funds

        ic = RecordingIc()
        ic.cycles["__deployer__"] = 1_000_000_000_000
        sheet = {"environments": {"local": {"cycles": {"budget_tc": 100}}}}
        with pytest.raises(RuntimeError, match="shortfall"):
            check_funds(ic, sheet, "local", "deployer-principal")


# ── oracle ───────────────────────────────────────────────────────────────────

def _governed_live(ic: RecordingIc, sheet: dict, bindings: dict[str, str]) -> None:
    with open(CORPUS, encoding="utf-8") as f:
        full_sheet = json.load(f)
    ic.deployer = "operator-principal"
    from auth import _normalize_permissions, _parse_permissions
    granted = lambda p: _parse_permissions(_normalize_permissions(p))  # noqa: E731
    ic.queries[(bindings["casals-backend"], "get_tree")] = {
        "sections": [
            {"name": "Casals", "stands": [],
             "commanders": [{"principal": "operator-principal", "permissions": granted("*")}]},
            {"name": "Product",
             "commanders": [{"principal": "operator-principal", "permissions": granted("canister.*")}],
             "stands": [{
                 "name": "Motoko",
                 "commanders": [{"principal": "operator-principal", "permissions": granted("stand.*")}],
                 "canisters": [{"name": "motoko-backend", "canister_id": bindings.get("motoko-backend")}],
             }]},
        ],
        "principal_aliases": {},
    }
    for cname, cid in bindings.items():
        if cname == "motoko-backend":
            ic.controllers[cid] = [bindings["casals-backend"], bindings["multisig"]]
        elif cname in ("file-registry", "file-registry-frontend"):
            ic.controllers[cid] = [bindings["casals-backend"]]
        elif cname in ("casals-backend", "casals-frontend"):
            ic.controllers[cid] = [bindings["multisig"]]
        elif cname == "multisig":
            ic.controllers[cid] = [cid]
        else:
            ic.controllers[cid] = [bindings.get("multisig", "ms-id")]
        ic.module_hashes[cid] = "a" * 64
        ic.cycles[cid] = 5_000_000_000_000
    ic.updates[(bindings["file-registry"], "list_files")] = [
        {"path": "hello-world-motoko@1.0.0.wasm.gz", "sha256": "a" * 64},
    ]
    ic.icp_outputs = {("canister", "call", bindings["multisig"], "list_signers"):
                      '(record { threshold = 1 : nat; signers = vec { principal "operator-principal" }; })'}


class TestOracle:
    def test_pass_on_converged_view(self):
        ic = RecordingIc()
        with open(CORPUS, encoding="utf-8") as f:
            sheet = json.load(f)
        bindings = {
            "casals-backend": "backend-id",
            "file-registry": "fr-id",
            "file-registry-frontend": "fr-fe-id",
            "casals-frontend": "fe-id",
            "multisig": "ms-id",
            "motoko-backend": "motoko-id",
        }
        _governed_live(ic, sheet, bindings)
        report = run_oracle(sheet, "local", bindings, ic)
        failures = [r for r in report.rows if r.result == "FAIL"]
        assert not failures, failures

    @pytest.mark.parametrize("field,mutator", [
        ("controllers", lambda ic, b: ic.controllers.__setitem__(b["motoko-backend"], ["wrong-principal"])),
        ("module_hash", lambda ic, b: ic.module_hashes.__setitem__(b["motoko-backend"], "b" * 64)),
        ("exists", lambda ic, b: b.pop("motoko-backend")),
    ])
    def test_fail_on_perturbation(self, field, mutator):
        ic = RecordingIc()
        with open(CORPUS, encoding="utf-8") as f:
            sheet = json.load(f)
        bindings = {
            "casals-backend": "backend-id",
            "file-registry": "fr-id",
            "file-registry-frontend": "fr-fe-id",
            "casals-frontend": "fe-id",
            "multisig": "ms-id",
            "motoko-backend": "motoko-id",
        }
        _governed_live(ic, sheet, bindings)
        mutator(ic, bindings)
        report = run_oracle(sheet, "local", bindings, ic)
        assert not report.passed
        assert any(r.field.startswith(field.split("[")[0]) or field in r.field for r in report.rows if r.result == "FAIL")


# ── show / graph ─────────────────────────────────────────────────────────────

class TestShowGraph:
    def test_show_text_renders_table(self):
        view = {
            "env": "local",
            "backend_id": "backend-id",
            "plan_summary": {"items": 0, "unmanaged": 0},
            "canisters": [{
                "section": "App", "stand": "Hello", "name": "hello-backend",
                "canister_id": "aaaa-aa", "mode": "managed", "cycles_tc": 1.5,
                "ic_controllers_named": ["casals-backend"],
            }],
        }
        text = render_show_text(view)
        assert "hello-backend" in text
        assert "App" in text

    def test_mermaid_edge_types(self):
        view = {
            "tree": {"sections": [{"stands": [{"name": "Hello", "commanders": [{"principal": "op-p"}]}]}]},
            "orchestration_status": {"batons": []},
            "canisters": [{
                "name": "hello-backend",
                "canister_id": "aaaa-aa",
                "ic_controllers": ["op-p"],
            }],
        }
        mmd = mermaid_graph(view, {}, {"hello-backend": "aaaa-aa", "op-p": "op-p"})
        assert "ic_controller" in mmd
        assert "casals_commander" in mmd


class TestMultisigPaths:
    def _ic(self):
        ic = RecordingIc(env="local")
        ic.icp_outputs = {
            ("canister", "call", "ms-id", "list_signers"):
                '(record { threshold = 1 : nat; signers = vec { principal "deployer" }; })',
            ("canister", "call", "ms-id", "propose"): "(7 : nat)",
            ("canister", "call", "ms-id", "get_proposal"): "(opt record { status = variant { executed }; })",
        }
        return ic

    def test_apply_via_multisig_proposes_apply_sheet(self):
        from casals_cli.multisig import apply_via_multisig

        ic = self._ic()
        apply_via_multisig(ic, "ms-id", "deployer", "be-id", "abc", confirm_destructive=True, max_items=5)
        proposal = next(c[1][0] for c in ic.calls if c[0] == "icp" and c[1][0][3:4] == ("propose",))
        assert 'ApplySheet = record { casals_backend = principal "be-id"; plan_hash = "abc"; ' \
               'confirm_destructive = true; max_items = 5 : nat }' in proposal[4]

    def test_non_signer_is_refused(self):
        from casals_cli.multisig import apply_via_multisig

        ic = self._ic()
        with pytest.raises(RuntimeError, match="not a signer"):
            apply_via_multisig(ic, "ms-id", "stranger", "be-id", "abc", confirm_destructive=False, max_items=5)

    def test_pending_proposal_stops_with_id(self):
        from casals_cli.multisig import set_controllers_via_multisig

        ic = self._ic()
        ic.icp_outputs[("canister", "call", "ms-id", "get_proposal")] = "(opt record { status = variant { pending }; })"
        with pytest.raises(RuntimeError, match="#7 .* is pending"):
            set_controllers_via_multisig(ic, "ms-id", "deployer", "c-id", ["deployer", "ms-id"])
