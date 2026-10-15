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
