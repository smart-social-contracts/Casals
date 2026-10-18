import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from casals_cli.normalize import normalize  # noqa: E402


def test_template_creator_gets_stand_create_on_its_section():
    sheet = {"version": 2, "name": "t", "environments": {"local": {"network": "local"}},
             "sections": [{"name": "Deployments", "arrangements": {"stand_template": {
                 "name_pattern": "*", "created_by": "aaaaa-aa", "canisters": []}}, "stands": []}]}
    doc = normalize(sheet, "local", deployer="2vxsx-fae", ids={}, uploads=[])
    assert {"scope": "section:Deployments", "principal": "aaaaa-aa", "permissions": ["stand.create"],
            "calls": []} in doc["grants"]


def test_mainnet_defaults_to_the_hosted_monitor():
    from casals_cli.sheetv2 import HOSTED_MONITOR_URL, validate

    def monitor(env_cfg):
        sheet = {"version": 2, "name": "t", "environments": {"e": env_cfg}, "sections": []}
        assert not any("monitor" in e for e in validate(sheet, "e"))
        return normalize(sheet, "e", deployer="2vxsx-fae", ids={}, uploads=[])["monitor"]

    assert monitor({"network": "ic"}) == {"principal": "", "url": HOSTED_MONITOR_URL}
    assert monitor({"network": "ic", "monitor": False}) == {}
    assert monitor({"network": "local"}) == {}
    own = {"principal": "aaaaa-aa", "url": "https://mon.example"}
    assert monitor({"network": "ic", "monitor": own}) == own


def test_up_asks_the_monitor_for_its_principal_and_registers(monkeypatch):
    import io
    import json
    import urllib.request

    from casals_cli import up

    seen = []

    def urlopen(req, timeout=0):
        url = req if isinstance(req, str) else req.full_url
        seen.append(url)
        if url.endswith("/v1/service"):
            if "down" in url:
                raise OSError("unreachable")
            return io.BytesIO(json.dumps({"principal": "mon"}).encode())
        res = io.BytesIO(b"{}")
        res.status = 201
        return res

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert up.monitor_principal({"principal": "", "url": "https://m"}) == {"principal": "mon", "url": "https://m"}
    assert up.monitor_principal({"principal": "", "url": "https://down"}) == {}
    assert up.monitor_principal({"principal": "p", "url": "https://x"})["principal"] == "p"
    assert "registered" in up.register_monitor({"monitor": {"url": "https://m/v1/abc"}}, "abc")
    assert seen[-1] == "https://m/v1/instances"
    assert up.register_monitor({"monitor": {}}, "abc") == "no monitor"


def test_the_sheet_sets_how_many_snapshots_to_keep():
    from casals_cli.sheetv2 import validate

    sheet = {"version": 2, "name": "t", "environments": {"local": {"network": "local"}}, "sections": []}
    assert normalize(sheet, "local", deployer="2vxsx-fae", ids={}, uploads=[])["snapshots_keep"] == 2
    sheet["snapshots"] = {"keep": 4}
    assert normalize(sheet, "local", deployer="2vxsx-fae", ids={}, uploads=[])["snapshots_keep"] == 4
    for bad, message in (({"keep": 10}, "snapshots.keep must be"), ({"keep": True}, "snapshots.keep must be"),
                         ([], "snapshots must be an object")):
        assert any(message in e for e in validate({**sheet, "snapshots": bad})), bad
    assert not any("snapshots" in e for e in validate(sheet))
