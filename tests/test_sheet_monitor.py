"""`environments.<env>.monitor`: the sheet names the off-chain monitor, the
conductor applies it at `set_sheet` (before anything is provisioned), and
`casals up` grants its read access where the conductor holds no control."""

import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from ic_python_db.db_engine import Database  # noqa: E402
from ic_python_db.storage import MemoryStorage  # noqa: E402

import sheetv2  # noqa: E402
from casals_cli.ic import RecordingIc  # noqa: E402
from casals_cli.monitor import grant_monitor_access  # noqa: E402

SELF = "zzzzz-zz"
MONITOR = "x5a7t-3syrx-klefo-c6ohm-gw4qj-lnkre-xv4fo-lxzw3-mpvz2-j4wr2-vae"


def _sheet(monitor=None):
    sheet = json.load(open(os.path.join(ROOT, "casals.json"), encoding="utf-8"))
    sheet["environments"]["staging"].pop("monitor", None)
    if monitor is not None:
        sheet["environments"]["staging"]["monitor"] = monitor
    return sheet


def test_monitor_block_is_validated_and_read():
    ok = _sheet({"principal": MONITOR, "url": "https://service.staging.example/"})
    assert sheetv2.validate(ok, "staging") == []
    assert sheetv2.env_monitor(ok, "staging") == {"principal": MONITOR, "url": "https://service.staging.example"}
    assert sheetv2.env_monitor(_sheet(), "staging") is None

    errors = sheetv2.validate(_sheet({"principal": "monitor", "url": "https://m.example/v1/abc", "poll": 5}), "staging")
    assert any("unknown field(s) poll" in e for e in errors)
    assert any("principal must be a principal id" in e for e in errors)
    assert any("base URL" in e for e in errors)


class _P:
    def __init__(self, v):
        self.v = v

    def to_str(self):
        return self.v


class _FakeIC:
    @staticmethod
    def id():
        return _P(SELF)

    @staticmethod
    def caller():
        return _P(SELF)

    @staticmethod
    def time():
        return 0


@pytest.fixture
def db(monkeypatch):
    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import main  # noqa: F401 — registers models / decorators
    import audit
    import cycles
    import helpers
    import sheet_api
    for mod in (audit, helpers, sheet_api):
        monkeypatch.setattr(mod, "ic", _FakeIC)
    armed = []
    monkeypatch.setattr(cycles, "_arm_autopilot", lambda: armed.append("autopilot"))
    monkeypatch.setattr(cycles, "_arm_cycle_sampler", lambda: armed.append("sampler"))
    monkeypatch.setattr(cycles, "refresh_cycles_snapshot_settings", lambda: None)
    yield armed
    Database._instance = prev


def test_set_sheet_applies_the_monitor_once(db):
    import sheet_api
    from helpers import _settings

    s = _settings()
    s.cycles_autopilot = 1
    s.cycles_sampling = 1
    monitor = {"principal": MONITOR, "url": "https://service.staging.example"}

    assert sheet_api.apply_sheet_monitor(monitor) is True
    s = _settings()
    assert (s.monitor_enabled, s.monitor_principal) == (1, MONITOR)
    assert s.monitor_service_url == f"https://service.staging.example/v1/{SELF}"
    assert (s.cycles_autopilot, s.cycles_sampling) == (0, 0), "off-chain mode, as Settings saves it"
    assert db == ["autopilot", "sampler"]

    assert sheet_api.apply_sheet_monitor(monitor) is False, "unchanged settings are left alone"
    assert sheet_api.apply_sheet_monitor(None) is False


def test_deploy_keeps_a_service_url_changed_in_settings(db):
    import sheet_api
    from helpers import _settings

    other = "aaaaa-aa"
    s = _settings()
    s.monitor_service_url = f"https://other-monitor.example/v1/{SELF}"
    s.monitor_principal = other
    s.monitor_enabled = 0
    monitor = {"principal": MONITOR, "url": "https://service.staging.example"}

    assert sheet_api.apply_sheet_monitor(monitor) is True, "still switches the monitor on"
    s = _settings()
    assert s.monitor_enabled == 1
    assert s.monitor_service_url == f"https://other-monitor.example/v1/{SELF}"
    assert s.monitor_principal == other


def test_settings_update_uses_icp_controller_flags():
    ic = RecordingIc()
    ic.icp = lambda argv, **kw: ic.record("icp", tuple(argv))
    from casals_cli.ic import IcClient
    IcClient.settings_update(ic, "c1", set_controllers=["a", "b"])
    IcClient.settings_update(ic, "c1", add_controllers=["x"], remove_controllers=["y"])
    assert [args[0] for op, args, _ in ic.calls] == [
        ("canister", "settings", "update", "c1", "-f", "--set-controller", "a", "--set-controller", "b"),
        ("canister", "settings", "update", "c1", "-f", "--add-controller", "x", "--remove-controller", "y"),
    ]


def test_up_lends_control_where_the_conductor_has_none_and_restores_it():
    backend, deployer, baton = "be-id", "dep-id", "baton-id"
    ic = RecordingIc(env="staging")
    ic.deployer = deployer
    ic.controllers = {
        backend: ["ms-id", deployer],          # the conductor does not control itself
        "fe-id": [backend, deployer],          # already Casals': sync handles it
        "front-id": [baton, deployer],         # handed off to a baton
        "sole-id": [baton],                    # nobody here can change it
    }
    before = {k: list(v) for k, v in ic.controllers.items()}
    seen = []

    def sync():
        held = sorted(c for c, ctl in ic.controllers.items() if backend in ctl)
        seen.append(held)
        return {"ok": True, "updated": [{"canister": c} for c in held], "skipped": [], "failed": []}

    ic.call_update = lambda cid, method, arg=None, **kw: sync()

    res = grant_monitor_access(ic, backend, deployer, "ms-id",
                               {"casals-backend": backend, "casals-frontend": "fe-id",
                                "motoko-frontend": "front-id", "motoko-backend": "sole-id"},
                               progress=lambda _m: None)

    assert seen == [["fe-id"], [backend, "fe-id", "front-id"]]
    assert sorted(res["lent"]) == ["casals-backend", "motoko-frontend"]
    assert [r["canister"] for r in res["unreachable"]] == ["motoko-backend"]
    assert ic.controllers == before, "every lent canister gets its controllers back"
