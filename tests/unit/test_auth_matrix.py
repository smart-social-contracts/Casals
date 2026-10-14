import pytest

from auth import KEYS
from fake_chain import ANONYMOUS
from kit import call, grant, world
from model import orchestra
from op import OPS

ARGS = {None: {}, "scope": {"scope": "stand:d1"}, "canister": {"canister": "c1"},
        "stand": {"stand": "d1"}, "section": {"section": "s1"}}
CALLERS = ["anon", "outsider", "other", "without", "with", "monitor", "ctl"]


def allowed(spec, who):
    perm = spec["perm"]
    if perm is None or who == "ctl":
        return True
    if who == "anon":
        return False
    if perm == "signed" or (spec["monitor"] and who == "monitor"):
        return True
    if perm == "reader":
        return who != "outsider"
    return perm != "controller" and who == "with"


@pytest.mark.parametrize("name", sorted(OPS))
@pytest.mark.parametrize("who", CALLERS)
def test_auth_matrix(name, who):
    spec = OPS[name]
    world()
    rung = "orchestra" if spec["at"] is None else "section:s1"
    perm = spec["perm"] if spec["perm"] in KEYS else "stand.edit"
    grant("other", "section:s2", "*")
    grant("without", rung, *(KEYS - {perm}))
    grant("with", rung, perm)
    orchestra().monitor = {"principal": "monitor"}
    reply = call(name, ANONYMOUS if who == "anon" else who, **ARGS[spec["at"][0] if isinstance(spec["at"], tuple) else spec["at"]])
    assert reply.get("error", "").startswith("unauthorized") != allowed(spec, who), reply
