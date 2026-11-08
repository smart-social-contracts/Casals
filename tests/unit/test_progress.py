import chain
import op
import progress
from fake_chain import state
from kit import call
from model import Stand
from test_content import bundle_files
from test_stands import TEMPLATE, tenants


def with_bundle(files):
    tenants()
    template = {**TEMPLATE, "canisters": [{**c, "content": "web/v1"} if c["name"].endswith("frontend") else c
                                          for c in TEMPLATE["canisters"]]}
    assert call("section_set", section="Tenants", template=template)["ok"]
    bundle_files("web/v1", files)


def shown(stand):
    return call("orchestra_get", stand=stand)["stands"][0]["progress"]


def run_rounds(stand, limit=None):
    """One timer at a time, advancing the clock; what orchestra_get shows after each round."""
    seen = []
    while (due := [t for t in state["timers"] if t[0] < 60]) and len(seen) != limit:
        state["now"] += 7
        state["timers"].remove(due[0])
        op.run(due[0][1]())
        seen.append(shown(stand))
    return seen


def test_a_stand_reports_each_step_of_its_build():
    with_bundle(25)
    call("stand_create", section="Tenants", name="t1")
    seen = run_rounds("t1")
    steps = [(p["step"], p["canister"], p["done"], p["total"]) for p in seen[:-1]]
    assert steps == [("provision", "t1-backend", 0, 2), ("provision", "t1-frontend", 1, 2),
                     ("content", "t1-frontend", 10, 26), ("content", "t1-frontend", 20, 26)]
    assert [p["at"] for p in seen[:-1]] == sorted({p["at"] for p in seen[:-1]})
    assert seen[-1] == {} and Stand["t1"].built_at


def test_a_stand_reports_each_canister_it_hands_off(monkeypatch):
    with_bundle(2)
    handed = []
    update_settings = chain.update_settings

    def spy(cid, **settings):
        if progress.of("t1")["step"] == "hand_off":
            handed.append((progress.of("t1")["canister"], progress.of("t1")["done"], progress.of("t1")["total"]))
        return update_settings(cid, **settings)

    monkeypatch.setattr(chain, "update_settings", spy)
    call("stand_create", section="Tenants", name="t1")
    run_rounds("t1")
    assert handed == [("t1-backend", 0, 2), ("t1-frontend", 1, 2)]


def test_a_failed_build_shows_its_error_instead_of_progress():
    with_bundle(2)
    call("stand_create", section="Tenants", name="t1")
    run_rounds("t1", limit=1)
    assert shown("t1")["step"] == "provision"
    state["fail"]["install"] = "trap"
    run_rounds("t1")
    assert shown("t1") == {} and "trap" in Stand["t1"].build_error


def test_a_deleted_stand_leaves_no_progress():
    with_bundle(2)
    call("stand_create", section="Tenants", name="t1")
    run_rounds("t1", limit=1)
    assert progress.builds
    call("stand_delete", stand="t1")
    assert progress.builds == {}
