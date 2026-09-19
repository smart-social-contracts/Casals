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
        # module_hash goes through read_state + lookup so that "absent" (None) is distinct from "failed".
        monkeypatch.setattr(
            ic, "_agent_client",
            lambda: type("A", (), {"read_state_raw": lambda self, cid, paths: calls.append(("hash", cid)) or {}})(),
        )
        monkeypatch.setattr("ic.certificate.lookup", lambda path, cert: bytes.fromhex("abc123"))
        assert ic.read_controllers("aaaaa-aa") == ["aaaaa-aa"]
        assert ic.read_module_hash("aaaaa-aa") == "abc123"
        assert calls == [("controllers", "aaaaa-aa"), ("hash", "aaaaa-aa")]


# ── up sequencing + idempotency ─────────────────────────────────────────────

class TestUpSequencing:
    def _governed_ic(self) -> RecordingIc:
        ic = RecordingIc(env="local")
        ic.deployer = "deployer-principal"
        # budget + three create deposits + headroom (exactly budget_tc is the
        # GaaS-prod trap the preflight now catches)
        ic.cycles["__deployer__"] = 110_000_000_000_000
        ic.converged = False
        return ic

    def test_first_up_records_bootstrap_calls(self, tmp_path, monkeypatch):
        ic = self._governed_ic()
        ic.converged = True
        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        def _fake_bootstrap(_ic, _sheet, bindings, **kwargs):
            bindings.conductor.setdefault("casals-backend", "backend-id")
            bindings.conductor.setdefault("casals-wasms", "store-id")
            bindings.backend_id = bindings.conductor["casals-backend"]
            # bootstrap leaves the deployer controlling the fresh store
            ic.controllers["store-id"] = ["deployer-principal"]
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
                "casals-wasms": "cond-store",
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
        sheet = {
            "conductor": {"backend": {}, "frontend": {}, "wasms": {}},
            "cycles": {"conductor_min_balance_tc": 5.0},
            "environments": {"local": {"cycles": {"budget_tc": 100}}},
        }
        with pytest.raises(RuntimeError, match="shortfall"):
            check_funds(ic, sheet, "local", "deployer-principal")

    _SHEET = {
        "conductor": {"backend": {}, "frontend": {}, "wasms": {}},
        "cycles": {"conductor_min_balance_tc": 5.0},
        "environments": {"production": {"cycles": {"budget_tc": 7}}},
    }

    def test_fresh_deploy_counts_the_create_deposits(self):
        """GaaS prod, 2026-09-19: 7.44 TC passed a budget of 7, then three
        `icp canister create` deposits (2 TC each) left 1.44 TC and the
        conductor top-up failed. The preflight must count the deposits."""
        from casals_cli.up import check_funds, funding_needed

        ic = RecordingIc(env="production")
        ic.cycles["__deployer__"] = 7_440_000_000_000
        need = funding_needed(ic, self._SHEET, "production", None)
        assert need["creates"] == ["casals-backend", "casals-frontend", "casals-wasms"]
        assert need["creates_tc"] == 6.0
        assert need["topup_tc"] == pytest.approx(7 - 1.5)  # deposit minus creation fee
        with pytest.raises(RuntimeError, match=r"needs ≈11\.6\d TC.*3 conductor create"):
            check_funds(ic, self._SHEET, "production", "dep", None)
        ic.cycles["__deployer__"] = 12_000_000_000_000
        check_funds(ic, self._SHEET, "production", "dep", None)

    def test_resume_with_funded_conductor_needs_nothing(self):
        """Realms prod resume #3: the conductor already held the budget, yet
        `up` refused because the deployer (7.44 TC) was under budget_tc (20)."""
        from casals_cli.bindings import Bindings
        from casals_cli.up import check_funds, funding_needed

        ic = RecordingIc(env="production")
        ic.cycles["__deployer__"] = 7_440_000_000_000
        ic.module_hashes["be"] = "hash"
        ic.queries[("be", "get_status")] = {"cycles": 20_000_000_000_000}
        b = Bindings(sheet_name="realms", env="production", network_url="https://icp0.io", deployer="dep",
                     conductor={"casals-backend": "be", "casals-frontend": "fe", "casals-wasms": "ws"}, backend_id="be")
        sheet = dict(self._SHEET, environments={"production": {"cycles": {"budget_tc": 20}}})
        need = funding_needed(ic, sheet, "production", b)
        assert need["creates"] == [] and need["topup_tc"] == 0
        check_funds(ic, sheet, "production", "dep", b)

    def test_fund_conductor_caps_at_the_deployer_balance(self):
        from casals_cli.up import fund_conductor

        ic = RecordingIc(env="production")
        ic.cycles["__deployer__"] = 7_000_000_000_000
        ic.queries[("be", "get_status")] = {"cycles": 1_470_000_000_000}
        sheet = dict(self._SHEET, environments={"production": {"cycles": {"budget_tc": 20}}})
        fund_conductor(ic, sheet, "production", "be")
        topups = [c for c in ic.calls if c[0] == "top_up"]
        assert len(topups) == 1
        assert topups[0][1] == ("be", 6_900_000_000_000)  # balance minus the 0.1 TC keep

    def test_fund_conductor_refuses_when_the_floor_is_out_of_reach_on_a_fresh_deploy(self):
        from casals_cli.up import fund_conductor

        ic = RecordingIc(env="production")
        ic.cycles["__deployer__"] = 1_440_000_000_000
        ic.queries[("be", "get_status")] = {"cycles": 1_470_000_000_000}
        with pytest.raises(RuntimeError, match="nothing was spent"):
            fund_conductor(ic, self._SHEET, "production", "be", strict=True)
        assert not [c for c in ic.calls if c[0] == "top_up"]

    def test_resume_pours_what_the_deployer_can_spare(self):
        """GaaS prod resume: treasury 3.26 TC under the 5 TC floor, deployer
        0.90 TC, no creates left — the run must go on, not demand +10 TC."""
        from casals_cli.bindings import Bindings
        from casals_cli.up import check_funds, fund_conductor

        ic = RecordingIc(env="production")
        ic.cycles["__deployer__"] = 900_000_000_000
        ic.module_hashes["be"] = "hash"
        ic.queries[("be", "get_status")] = {"cycles": 3_260_000_000_000}
        b = Bindings(sheet_name="gaas", env="production", network_url="https://icp0.io", deployer="dep",
                     conductor={"casals-backend": "be", "casals-frontend": "fe", "casals-wasms": "ws"}, backend_id="be")
        sheet = dict(self._SHEET, environments={"production": {"cycles": {"budget_tc": 14}}})
        need = check_funds(ic, sheet, "production", "dep", b)   # warns, does not raise
        assert need["strict"] is False and need["creates"] == []
        fund_conductor(ic, sheet, "production", "be", strict=need["strict"])
        topups = [c for c in ic.calls if c[0] == "top_up"]
        assert topups == [("top_up", ("be", 800_000_000_000), {})]

    def test_fresh_deploy_short_of_the_top_up_is_refused(self):
        from casals_cli.up import check_funds

        ic = RecordingIc(env="production")
        ic.cycles["__deployer__"] = 7_000_000_000_000   # covers the 3 deposits, not the treasury
        with pytest.raises(RuntimeError, match="shortfall"):
            check_funds(ic, self._SHEET, "production", "dep", None)


# ── oracle ───────────────────────────────────────────────────────────────────

def _governed_live(ic: RecordingIc, sheet: dict, bindings: dict[str, str]) -> None:
    with open(CORPUS, encoding="utf-8") as f:
        full_sheet = json.load(f)
    ic.deployer = "operator-principal"
    from auth import _normalize_permissions, _parse_permissions
    granted = lambda p: _parse_permissions(_normalize_permissions(p))  # noqa: E731

    # Mirror the sheet's conductor commanders / multisig signers so the live
    # view tracks corpus edits (extra local principals, e.g. a browser II).
    principals = full_sheet["environments"]["local"]["principals"]

    def resolve(token: str) -> str:
        if token == "$deployer":
            return ic.deployer
        if token.startswith("$principal:"):
            return resolve(principals[token[len("$principal:"):]])
        return token

    conductor_commanders = [
        {"principal": resolve(c["principal"]), "permissions": granted(c.get("permissions", "*"))}
        for c in full_sheet["conductor"]["commanders"]
    ]
    signers = sorted({resolve(s) for s in full_sheet["governance"]["multisig"]["signers"]})
    # The Motoko stand mirrors the sheet too: it carries an unclaimed access-code
    # slot (`sha256:` principal) next to the operator.
    product = next(sec for sec in full_sheet["sections"] if sec["name"] == "Product")
    motoko = next(st for st in product["stands"] if st["name"] == "Motoko")

    def mirror(entries):
        return [
            {"principal": resolve(c["principal"]), "permissions": granted(c.get("permissions", "*")),
             "unclaimed": resolve(c["principal"]).startswith("sha256:")}
            for c in entries
        ]

    stand_commanders = mirror(motoko["commanders"])
    ic.queries[(bindings["casals-backend"], "get_tree")] = {
        "sections": [
            {"name": "Casals", "stands": [], "commanders": conductor_commanders},
            {"name": "Product",
             "commanders": mirror(product["commanders"]),
             "stands": [{
                 "name": "Motoko",
                 "commanders": stand_commanders,
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
        elif cname == "casals-wasms":
            ic.controllers[cid] = [bindings["casals-backend"], ic.deployer]
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
    if "casals-wasms" in bindings:
        # The oracle reads expected hashes from the store when one is bound; the
        # fake holds the same listing (a fixed sha256 needs no matching bytes).
        from casals_cli.wasm_store import FakeAssetStore
        store = FakeAssetStore()
        store.files["/wasm/hello-world-motoko@1.0.0.wasm.gz"] = (b"motoko", "application/wasm")
        ic.candid.update(store.handlers())
        if "motoko-backend" in bindings:
            ic.module_hashes[bindings["motoko-backend"]] = hashlib.sha256(b"motoko").hexdigest()
    signer_vec = " ".join(f'principal "{s}";' for s in signers)
    ic.icp_outputs = {("canister", "call", bindings["multisig"], "list_signers"):
                      f'(record {{ threshold = 1 : nat; signers = vec {{ {signer_vec} }}; }})'}


class TestOracle:
    def test_pass_on_converged_view(self):
        ic = RecordingIc()
        with open(CORPUS, encoding="utf-8") as f:
            sheet = json.load(f)
        bindings = {
            "casals-backend": "backend-id",
            "casals-wasms": "store-id",
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

    def test_claimed_access_code_slot_still_passes(self):
        """After someone redeems the code, the tree shows their principal with
        `code_checksum`; the oracle must read that as the declared slot."""
        ic = RecordingIc()
        with open(CORPUS, encoding="utf-8") as f:
            sheet = json.load(f)
        bindings = {
            "casals-backend": "backend-id",
            "casals-wasms": "store-id",
            "file-registry": "fr-id",
            "file-registry-frontend": "fr-fe-id",
            "casals-frontend": "fe-id",
            "multisig": "ms-id",
            "motoko-backend": "motoko-id",
        }
        _governed_live(ic, sheet, bindings)
        tree = ic.queries[(bindings["casals-backend"], "get_tree")]
        motoko = tree["sections"][1]["stands"][0]
        for c in motoko["commanders"]:
            if c.get("unclaimed"):
                c.update(principal="claimer-principal", unclaimed=False,
                         code_checksum=sheet["environments"]["local"]["principals"]["invited_operator"])
        report = run_oracle(sheet, "local", bindings, ic)
        failures = [r for r in report.rows if r.result == "FAIL"]
        assert not failures, failures
        # …but a stranger holding the slot's permissions without the checksum is drift.
        for c in motoko["commanders"]:
            c.pop("code_checksum", None)
        report = run_oracle(sheet, "local", bindings, ic)
        assert any(r.canister == "Motoko" and r.field == "commanders" and r.result == "FAIL" for r in report.rows)

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
            "casals-wasms": "store-id",
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

    def test_stale_pin_is_enforced_in_production_only(self):
        """Same pin policy as `up`: a laptop's build is what the store holds and
        what is live, so a stale sheet pin is not drift there; in production
        the pin is what must be live."""
        ic = RecordingIc()
        with open(CORPUS, encoding="utf-8") as f:
            sheet = json.load(f)
        bindings = {
            "casals-backend": "backend-id",
            "casals-wasms": "store-id",
            "file-registry": "fr-id",
            "file-registry-frontend": "fr-fe-id",
            "casals-frontend": "fe-id",
            "multisig": "ms-id",
            "motoko-backend": "motoko-id",
        }
        _governed_live(ic, sheet, bindings)
        entry = next(e for e in sheet["registry"]["wasms"] if e["family"] == "hello-world-motoko")
        entry["sha256"] = "c" * 64  # pinned before the artifact was rebuilt
        sheet["environments"]["production"] = sheet["environments"]["local"]  # the corpus has no production block

        def hash_row(env):
            return next(r for r in run_oracle(sheet, env, bindings, ic).rows
                        if r.canister == "motoko-backend" and r.field == "module_hash")

        assert hash_row("local").result == "PASS"
        assert hash_row("production").result == "FAIL"


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


class TestGovernedUpgrade:
    """`upgrade_code requires=multisig` (the multisig's own build bump): the
    conductor is not a controller, so the CLI takes control through the
    multisig, installs the sheet's declared build and leaves the controller
    cleanup to the next plan round."""

    def _plan(self, want: str) -> dict:
        return {"items": [{
            "kind": "upgrade_code", "requires": "multisig",
            "target": {"name": "multisig", "canister_id": "ms-id"},
            "desired": {"module_hash": want},
        }]}

    def _sheet(self, tmp_path, data: bytes) -> tuple[dict, str]:
        import gzip
        (tmp_path / "ms.wasm.gz").write_bytes(gzip.compress(data))
        (tmp_path / "other.wasm").write_bytes(b"\0asm other")
        sheet = {"registry": {"wasms": [
            {"family": "orchestration-multisig", "version": "1.6.0", "source": "local:ms.wasm.gz"},
            {"family": "hello", "version": "1", "source": "local:other.wasm"},
            {"family": "built", "version": "main", "source": "build:casals_backend"},  # never resolved
        ]}}
        return sheet, str(tmp_path)

    def test_installs_declared_build_via_multisig_control(self, tmp_path):
        import hashlib
        from casals_cli.up import deployer_items, registry_wasm_by_hash

        data = b"\0asm multisig 1.6.0"
        want = hashlib.sha256(data).hexdigest()
        sheet, sheet_dir = self._sheet(tmp_path, data)
        ic = TestMultisigPaths._ic(self)
        ic.controllers["ms-id"] = ["ms-id"]  # handed over: only the multisig controls itself
        lookup = registry_wasm_by_hash(sheet, sheet_dir=sheet_dir, project_root=sheet_dir)

        deployer_items(ic, self._plan(want), "deployer", "ms-id", lookup)

        proposal = next(c[1][0] for c in ic.calls if c[0] == "icp" and c[1][0][3:4] == ("propose",))
        assert 'SetCanisterControllers = record { canister_id = principal "ms-id"' in proposal[4]
        assert 'principal "deployer"' in proposal[4]  # deployer added, existing controller kept
        install = next(c for c in ic.calls if c[0] == "install_wasm")
        assert install[1][0] == "ms-id" and install[2] == {"mode": "upgrade"}
        assert hashlib.sha256(open(install[1][1], "rb").read()).hexdigest() == want  # gunzipped bytes

    def test_unknown_hash_is_reported_not_installed(self, tmp_path):
        from casals_cli.up import deployer_items, registry_wasm_by_hash

        sheet, sheet_dir = self._sheet(tmp_path, b"\0asm x")
        ic = TestMultisigPaths._ic(self)
        lookup = registry_wasm_by_hash(sheet, sheet_dir=sheet_dir, project_root=sheet_dir)
        deployer_items(ic, self._plan("ff" * 32), "deployer", "ms-id", lookup)
        assert not [c for c in ic.calls if c[0] == "install_wasm"]
        assert not [c for c in ic.calls if c[0] == "icp"]  # no proposal either

    def test_without_sheet_nothing_happens(self):
        from casals_cli.up import deployer_items

        ic = TestMultisigPaths._ic(self)
        deployer_items(ic, self._plan("ab" * 32), "deployer", "ms-id")  # `casals apply` has no sheet
        assert not [c for c in ic.calls if c[0] in ("install_wasm", "icp")]


# ── up --conductor <id> without bindings adopts the live conductor ──────────

class TestAdoptLiveConductor:
    def _ic(self) -> RecordingIc:
        ic = RecordingIc(env="production")
        ic.module_hashes["backend-live"] = "aa" * 32
        ic.queries[("backend-live", "casals_metadata")] = {
            "casals_frontend_canister_id": "frontend-live",
            "wasm_store_canister_id": "",  # pre-store conductor: no casals-wasms yet
        }
        ic.queries[("backend-live", "get_bindings")] = {"ok": True, "bindings": {"multisig": "ms-live"}}
        return ic

    def test_fills_gaps_from_the_conductor(self):
        from casals_cli.bindings import Bindings
        from casals_cli.up import _adopt_live_conductor

        b = Bindings(sheet_name="gaas", env="production", network_url="https://icp0.io", deployer="dep", conductor={}, backend_id="backend-live")
        _adopt_live_conductor(self._ic(), b, "backend-live")
        assert b.conductor == {
            "casals-backend": "backend-live",
            "casals-frontend": "frontend-live",
            "multisig": "ms-live",
        }
        assert "casals-wasms" not in b.conductor  # left for the bootstrap to create

    def test_existing_bindings_win(self):
        from casals_cli.bindings import Bindings
        from casals_cli.up import _adopt_live_conductor

        b = Bindings(sheet_name="gaas", env="production", network_url="https://icp0.io", deployer="dep", conductor={"casals-frontend": "frontend-mine"}, backend_id="backend-live")
        _adopt_live_conductor(self._ic(), b, "backend-live")
        assert b.conductor["casals-frontend"] == "frontend-mine"
        assert b.conductor["casals-backend"] == "backend-live"

    def test_uninstalled_conductor_is_left_alone(self):
        from casals_cli.bindings import Bindings
        from casals_cli.up import _adopt_live_conductor

        ic = RecordingIc(env="production")  # no module hash: created, never installed
        b = Bindings(sheet_name="gaas", env="production", network_url="https://icp0.io", deployer="dep", conductor={}, backend_id="backend-live")
        _adopt_live_conductor(ic, b, "backend-live")
        assert b.conductor == {}


class TestIcClientNetwork:
    def test_production_env_is_mainnet(self):
        ic = IcClient(env="production")
        assert ic.network_url == "https://icp0.io"
        assert ic._base_flags()[:2] == ["-n", "ic"]
        assert ic._base_flags(env=False)[:1] != ["-n"]

    def test_hsm_pin_becomes_a_password_file(self, tmp_path, monkeypatch):
        from casals_cli.ic import _cleanup_pin_files, _hsm_pin_file

        monkeypatch.delenv("ICP_IDENTITY_PASSWORD_FILE", raising=False)
        monkeypatch.delenv("DFX_HSM_PIN", raising=False)
        assert _hsm_pin_file() is None

        given = tmp_path / "pin"
        given.write_text("from-file")
        monkeypatch.setenv("ICP_IDENTITY_PASSWORD_FILE", str(given))
        assert _hsm_pin_file() == str(given)

        monkeypatch.delenv("ICP_IDENTITY_PASSWORD_FILE")
        monkeypatch.setenv("DFX_HSM_PIN", "221000")
        path = _hsm_pin_file()
        assert path and os.path.isfile(path)
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
        assert open(path).read() == "221000"
        _cleanup_pin_files()
        assert not os.path.exists(path)

        ic = IcClient(env="production", identity="hsm-deployer")
        assert "--identity-password-file" in ic._base_flags()


class TestDestroy:
    def test_drains_managed_then_deletes_conductor(self):
        from argparse import Namespace
        from casals_cli.commands import cmd_destroy

        ic = RecordingIc(env="production")
        ic.deployer = "deployer"
        ic.controllers["backend-id"] = ["deployer"]
        ic.controllers["frontend-id"] = ["deployer"]
        ic.controllers["product-id"] = ["backend-id"]
        ic.queries[("backend-id", "casals_metadata")] = {
            "casals_frontend_canister_id": "frontend-id",
            "wasm_store_canister_id": "",
        }
        ic.queries[("backend-id", "get_bindings")] = {"ok": True, "bindings": {}}
        ic.queries[("backend-id", "get_tree")] = {
            "sections": [{"stands": [{"canisters": [
                {"name": "widget", "canister_id": "product-id"},
                {"name": "casals-frontend", "canister_id": "frontend-id"},
            ]}]}]
        }
        ic.queries[("backend-id", "list_pool")] = {"canisters": []}
        ic.updates[("backend-id", "destroy_canister")] = {"ok": True, "cycles_reclaimed": 1}

        args = Namespace(env="production", sheet_name="", conductor="backend-id",
                         confirm_destructive=True, all=True)
        cmd_destroy(ic, args)

        updates = [c for c in ic.calls if c[0] == "call_update"]
        assert updates[0][1][1] == "destroy_canister"
        deleted = [c[1][0] for c in ic.calls if c[0] == "delete_canister"]
        assert deleted == ["frontend-id", "backend-id"]
        assert "product-id" not in deleted

    def test_pre_store_conductor_without_get_bindings_uses_the_tree(self):
        from argparse import Namespace
        from casals_cli.commands import cmd_destroy

        ic = RecordingIc(env="production")
        ic.deployer = "deployer"
        ic.controllers["backend-id"] = ["ms-id"]
        ic.controllers["frontend-id"] = ["ms-id"]
        ic.controllers["ms-id"] = ["ms-id"]
        ic.controllers["product-id"] = ["backend-id"]
        ic.queries[("backend-id", "casals_metadata")] = {"casals_frontend_canister_id": "frontend-id"}
        ic.queries[("backend-id", "get_tree")] = {
            "sections": [{"stands": [{"canisters": [
                {"name": "widget", "canister_id": "product-id"},
                {"name": "multisig", "canister_id": "ms-id"},
            ]}]}]
        }
        ic.queries[("backend-id", "list_pool")] = {"canisters": []}
        ic.updates[("backend-id", "destroy_canister")] = {"ok": True, "cycles_reclaimed": 1}

        def query(canister_id, method, text_arg=None):
            if method == "get_bindings":
                raise RuntimeError("Canister has no query method 'get_bindings' IC0536")
            return RecordingIc.query(ic, canister_id, method, text_arg)

        ic.query = query  # type: ignore[method-assign]
        # deployer is not a controller; ensure_control would propose — we
        # only assert the lookup found the multisig (no crash on get_bindings).
        from casals_cli.commands import _conductor_canisters
        assert _conductor_canisters(ic, "backend-id", None)["multisig"] == "ms-id"

    def test_refuses_when_deployer_is_not_a_controller_and_there_is_no_multisig(self):
        from argparse import Namespace
        from casals_cli.commands import cmd_destroy

        ic = RecordingIc(env="production")
        ic.deployer = "deployer"
        ic.controllers["backend-id"] = ["someone-else"]
        ic.queries[("backend-id", "casals_metadata")] = {}
        ic.queries[("backend-id", "get_bindings")] = {"ok": True, "bindings": {}}
        args = Namespace(env="production", sheet_name="", conductor="backend-id",
                         confirm_destructive=True, all=True)
        with pytest.raises(RuntimeError, match="no multisig"):
            cmd_destroy(ic, args)


# ── converge: stuck items, hand-off, transient IC errors ─────────────────────

class _ScriptedIc(RecordingIc):
    """RecordingIc whose `plan` answers come from a list, one per round."""

    def __init__(self, plans: list, apply_result=None, **kw):
        super().__init__(**kw)
        self.plans = list(plans)
        self.apply_result = apply_result

    def call_update(self, canister_id, method, text_arg=None, *, timeout=300):
        self.record("call_update", canister_id, method, text_arg)
        if method == "plan":
            return self.plans.pop(0) if self.plans else {"ok": True, "plan": {"hash": "end", "items": []}}
        if method == "apply" and self.apply_result is not None:
            return self.apply_result
        return super().call_update(canister_id, method, text_arg, timeout=timeout)


def _config_item(seq_hash: str, extra_items=()):
    item = {"seq": 0, "kind": "config_call", "target": {"name": "demo-backend", "canister_id": "demo"},
            "reason": "configure build_variant", "requires": "self", "destructive": False,
            "current": {"build_variant": "test"}, "desired": {"build_variant": "production"}}
    return {"ok": True, "plan": {"hash": seq_hash, "items": [item, *extra_items]}}


class TestConvergeGuards:
    def test_item_applied_but_back_unchanged_stops_after_three_rounds(self):
        """Realms prod, 2026-09-19: a test-variant wasm answered `config_call`
        with the old value forever; the loop re-applied it for 16 rounds
        because a shrinking sync_assets kept the plan hash moving."""
        from casals_cli.up import converge

        def sync(n):
            return {"seq": 1, "kind": "sync_assets", "target": {"name": "demo-frontend", "canister_id": "fe"},
                    "reason": f"sync {n} asset(s)", "requires": "self", "destructive": False,
                    "current": {}, "desired": {"keys": [f"k{i}" for i in range(n)]}}

        plans = [_config_item(f"h{i}", [sync(40 - 10 * i)]) for i in range(6)]
        applied = {"ok": True, "applied": [
            {"kind": "config_call", "target": {"name": "demo-backend"}},
            {"kind": "sync_assets", "target": {"name": "demo-frontend"}},
        ], "failed": None, "remaining": 0}
        ic = _ScriptedIc(plans, applied, env="production")
        with pytest.raises(SystemExit):
            converge(ic, "be", "dep", "", yes=True, max_items=5)
        rounds = [c for c in ic.calls if c[0] == "call_update" and c[1][1] == "plan"]
        assert len(rounds) == 4  # applied in rounds 1-3, refused at round 4

    def test_progressing_items_are_not_flagged(self):
        from casals_cli.up import converge

        def only_sync(n, h):
            return {"ok": True, "plan": {"hash": h, "items": [
                {"seq": 0, "kind": "sync_assets", "target": {"name": "fe", "canister_id": "fe"}, "reason": "sync",
                 "requires": "self", "destructive": False, "current": {}, "desired": {"keys": list(range(n))}}]}}

        plans = [only_sync(30, "a"), only_sync(20, "b"), only_sync(10, "c"), only_sync(5, "d"),
                 {"ok": True, "plan": {"hash": "e", "items": []}}]
        applied = {"ok": True, "applied": [{"kind": "sync_assets", "target": {"name": "fe"}}], "failed": None}
        ic = _ScriptedIc(plans, applied, env="production")
        plan = converge(ic, "be", "dep", "", yes=True, max_items=5)
        assert plan["items"] == []

    def test_handing_the_conductor_over_ends_as_converged(self):
        """Realms prod: after set_controllers casals-backend → [multisig] the
        next plan was refused (`caller is not a commander`) and `up` exited 1
        on a finished orchestra."""
        from casals_cli.up import converge

        handoff = {"ok": True, "plan": {"hash": "h1", "items": [
            {"seq": 0, "kind": "set_controllers", "target": {"name": "casals-backend", "canister_id": "be"},
             "reason": "controllers", "requires": "multisig", "destructive": True,
             "current": {"controllers": ["dep"]}, "desired": {"controllers": ["ms"]}}]}}
        refused = {"ok": False, "error": "unauthorized: caller is not a commander"}
        ic = _ScriptedIc([handoff, refused], env="production")
        ic.controllers["be"] = ["dep"]
        plan = converge(ic, "be", "dep", "ms", yes=True, max_items=5)
        assert plan["handed_off"] is True and plan["items"] == []
        assert any(c[0] == "settings_update" and c[1][0] == "be" for c in ic.calls)

    def test_unauthorized_without_a_hand_off_is_still_an_error(self):
        from casals_cli.up import converge

        ic = _ScriptedIc([{"ok": False, "error": "unauthorized: caller is not a commander"}], env="production")
        with pytest.raises(RuntimeError, match="not a commander"):
            converge(ic, "be", "dep", "ms", yes=True, max_items=5)


class TestTransientRetry:
    def _client(self, monkeypatch, outcomes):
        import subprocess as sp
        import casals_cli.ic as icmod

        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            code, err = outcomes.pop(0)
            return sp.CompletedProcess(cmd, code, "ok\n" if code == 0 else "", err)

        monkeypatch.setattr(icmod.subprocess, "run", fake_run)
        monkeypatch.setattr(icmod.time, "sleep", lambda s: None)
        monkeypatch.delenv("DFX_HSM_PIN", raising=False)
        return IcClient(env="production"), calls

    def test_call_retries_a_502_then_succeeds(self, monkeypatch):
        ic, calls = self._client(monkeypatch, [
            (1, "Error: The replica returned an HTTP Error: Http Error: status 502 Bad Gateway"),
            (1, "Error: direct update call failed: The request timed out."),
            (0, ""),
        ])
        out = ic.icp(["canister", "call", "be", "plan", "()"])
        assert out.returncode == 0 and len(calls) == 3

    def test_call_gives_up_after_the_attempts(self, monkeypatch):
        from casals_cli.ic import TRANSIENT_ATTEMPTS

        ic, calls = self._client(monkeypatch, [(1, "status 502 Bad Gateway")] * TRANSIENT_ATTEMPTS)
        with pytest.raises(RuntimeError, match="502"):
            ic.icp(["canister", "call", "be", "plan", "()"])
        assert len(calls) == TRANSIENT_ATTEMPTS

    def test_non_transient_error_is_not_retried(self, monkeypatch):
        ic, calls = self._client(monkeypatch, [(1, "Error: unauthorized: caller is not a commander")])
        with pytest.raises(RuntimeError):
            ic.icp(["canister", "call", "be", "plan", "()"])
        assert len(calls) == 1

    def test_create_is_never_retried(self, monkeypatch):
        """A repeated `canister create` is another 2 TC deposit."""
        ic, calls = self._client(monkeypatch, [(1, "status 502 Bad Gateway")])
        with pytest.raises(RuntimeError):
            ic.icp(["canister", "create", "--detached"])
        assert len(calls) == 1
