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
    from casals_cli.sheetv2 import HOSTED_MONITOR, validate

    def monitor(env_cfg):
        sheet = {"version": 2, "name": "t", "environments": {"e": env_cfg}, "sections": []}
        assert not any("monitor" in e for e in validate(sheet, "e"))
        return normalize(sheet, "e", deployer="2vxsx-fae", ids={}, uploads=[])["monitor"]

    assert monitor({"network": "ic"}) == HOSTED_MONITOR
    assert monitor({"network": "ic", "monitor": False}) == {}
    assert monitor({"network": "local"}) == {}
    own = {"principal": "aaaaa-aa", "url": "https://mon.example"}
    assert monitor({"network": "ic", "monitor": own}) == own


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
