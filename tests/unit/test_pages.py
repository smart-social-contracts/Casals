import json
import pathlib
import re

import model
from casals_cli.ic import query_all
from ic_python_db import Database
from kit import call, grant, world
from model import INDEXES, Canister, Grant, Section, Stand, Wasm, first, matching, reindex_batch, set_build

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


class Conductor:
    """What ``casals_cli.ic.query_all`` reads through: each query is an op run in process."""

    def query(self, canister_id, method, text_arg):
        return call(method, **json.loads(text_arg))


def pages(op, **args):
    return query_all(Conductor(), "conductor", op, args)


def many():
    world()
    for n in range(7):
        st = Stand(name=f"x{n}")
        st.section = Section["s1"]
        Canister(name=f"x{n}-be", canister_id=f"x{n}-id").stand = st
        grant(f"p{n}", f"stand:x{n}", "canister.deploy")
        Wasm(key=f"app@{n}", sha256=f"{n:02d}" * 32)


def test_list_ops_page_and_their_pages_add_up():
    many()
    whole = call("orchestra_get")
    assert whole["next"] == {"canisters": None, "sections": None, "stands": None}
    first_page = call("orchestra_get", count=3)
    assert len(first_page["canisters"]) == 3 and first_page["next"]["canisters"] == 4
    merged = pages("orchestra_get", count=3)
    for key in ("canisters", "sections", "stands"):
        assert merged[key] == whole[key], key
    assert "next" not in merged
    assert pages("cycles_get", count=2)["canisters"] == call("cycles_get")["canisters"]
    assert pages("grant_list", count=2)["grants"] == call("grant_list")["grants"]
    assert pages("wasm_list", count=2)["wasms"] == call("wasm_list")["wasms"]
    assert call("wasm_list", count=10**6)["next"] == {"wasms": None}


def test_list_reads_default_to_small_pages_and_halve_past_the_instruction_limit():
    many()
    asked = []

    class Limited(Conductor):
        """Refuses a page of more than 2 rows, as the IC refuses a query past its instruction limit."""

        def query(self, canister_id, method, text_arg):
            asked.append(json.loads(text_arg).get("count"))
            if asked[-1] and asked[-1] > 2:
                raise RuntimeError("Canister exceeded the limit of 5000000000 instructions for single message execution.")
            return super().query(canister_id, method, text_arg)

    out = query_all(Limited(), "conductor", "orchestra_get")
    assert out["canisters"] == call("orchestra_get")["canisters"]
    assert asked[:4] == [25, 12, 6, 3] and set(asked[4:]) == {1}
    asked.clear()
    query_all(Limited(), "conductor", "orchestra_events", {"take": 5})
    assert asked == [None]


def test_export_pages_merge_by_section():
    many()
    whole = call("orchestra_export")
    split = call("orchestra_export", count=2, sections_from=None, stands_from=3)
    assert split["sections"] and all(set(sec) == {"name", "stands"} for sec in split["sections"])
    out = pages("orchestra_export", count=2)
    for key in ("wasms", "grants", "sections"):
        assert out[key] == whole[key], key


def test_one_stand_and_one_canister_are_read_without_paging():
    many()
    got = call("orchestra_get", stand="x3")
    assert [c["name"] for c in got["canisters"]] == ["x3-be"] and [s["name"] for s in got["stands"]] == ["x3"]
    assert got["sections"] == [] and not any(got["next"].values())
    assert call("orchestra_get", "p3", stand="x3")["stands"][0]["name"] == "x3"
    assert call("orchestra_get", "p3", stand="x4")["stands"] == call("orchestra_get", stand="nope")["stands"] == []
    rows = call("cycles_get", canister="x5-be")
    assert [c["name"] for c in rows["canisters"]] == ["x5-be"] and rows["treasury"] and rows["next"] == {"canisters": None}
    assert call("cycles_get", canister="nope")["canisters"] == []


def test_lookups_follow_the_index():
    many()
    assert [g.key for g in matching(Grant, "principal", "p3")] == ["stand:x3|p3"]
    assert first(Canister, "canister_id", "x5-id").name == "x5-be"
    assert first(Wasm, "sha256", "04" * 32).key == "app@4"
    assert [s.name for s in matching(Stand, "state", "pending")][:2] == ["d1", "d2"]
    set_build(Stand["x2"], 0, "boom")
    assert [s.name for s in matching(Stand, "state", "failed")] == ["x2"]
    assert len(matching(Stand, "state", "pending", limit=2)) == 2


def test_rows_from_before_the_indexes_are_found_until_and_after_the_backfill():
    many()
    set_build(Stand["x1"], 7)
    db = Database.get_instance()
    for cls, field, _ in INDEXES:
        for key in [k for k in db._db_storage.keys() if k.startswith(f"_fi:{cls.get_full_type_name()}:{field}:")]:
            db._db_storage.remove(key) if hasattr(db._db_storage, "remove") else db._db_storage.pop(key)
    for s in Stand.load_some(1, 100):
        data = db.load(s._type, s._id)
        data.pop("_prop_state", None), data.pop("state", None)
        db.save(s._type, s._id, data)
    model._system("indexes", 0)
    assert first(Canister, "canister_id", "x5-id").name == "x5-be"
    assert "x1" not in [s.name for s in matching(Stand, "state", "pending")]
    set_build(Stand["x2"], 0, "boom")
    for _ in range(50):
        if reindex_batch():
            break
    assert model._system("indexes") == len(INDEXES) and model.reindex_pending() == []
    assert first(Canister, "canister_id", "x5-id").name == "x5-be"
    assert [g.key for g in matching(Grant, "scope", "stand:x6")] == ["stand:x6|p6"]
    assert [s.name for s in matching(Stand, "state", "failed")] == ["x2"]
    pending = [s.name for s in matching(Stand, "state", "pending")]
    assert "x1" not in pending and "x2" not in pending and "x3" in pending
    assert not [k for k in db._db_storage.keys() if k.startswith(f"_fi:{Stand.get_full_type_name()}:state:N")]


def test_new_stands_start_pending_and_loading_keeps_their_state():
    world()
    st = Stand(name="fresh")
    st.section = Section["s1"]
    assert first(Stand, "state", "pending") is not None and "fresh" in [s.name for s in matching(Stand, "state", "pending")]
    set_build(st, 5)
    assert Stand.load(st._id).state is None and "fresh" not in [s.name for s in matching(Stand, "state", "pending")]
    assert Stand(name="old", built_at=3).state is None and Stand(name="bad", build_error="x").state == "failed"


def test_no_canister_code_reads_a_whole_table():
    """Lists are paged, lookups indexed: nothing loads every row of a table in one message."""
    banned = re.compile(r"\brows\(|\.instances\(\)|load_some\(\s*1\s*,\s*\w+\.max_id\(\)")
    hits = [f"{p.name}:{n}" for p in SRC.glob("*.py") for n, line in enumerate(p.read_text().splitlines(), 1)
            if banned.search(line)]
    assert hits == [], hits
    assert json.dumps(call("orchestra_get")["next"])


def props_of(n, headers=1_300):
    """What content sync records for a realm frontend: the same long headers on each of ~200 assets."""
    csp = "default-src 'self';" + "script-src 'self' https://*.icp0.io;" * (headers // 36)
    return {f"/_app/immutable/chunks/{i}.js": {"headers": {"Content-Security-Policy": csp}} for i in range(n)}


def test_applied_props_live_beside_the_row_and_leave_it_when_set_again():
    world()
    st = Stand(name="fe")
    st.section = Section["s1"]
    c = Canister(name="fe-front", canister_id="fe-id", kind="frontend")
    c.stand = st
    assert c.applied_props() == {} and model.AssetProps.load(c._id) is None
    c.set_applied_props({})
    assert model.AssetProps.load(c._id) is None  # nothing to keep
    props = props_of(3)
    c.set_applied_props(props)
    assert c.applied_props() == props and model.AssetProps.load(c._id).props == props
    assert "/_app" not in Database.get_instance().load(c._type, c._id).get("assets_props", "")
    c.set_applied_props({"/a": {}})
    assert Canister.load(c._id).applied_props() == {"/a": {}}
    c.delete()
    assert model.AssetProps.load(c._id) is None


def test_applied_props_kept_in_the_row_by_an_earlier_conductor_are_read_and_moved_by_the_backfill():
    many()
    props = props_of(5)
    held = Canister["x2-be"]
    held.assets_props = props  # as an earlier conductor stored it
    other = Canister["x3-be"]
    other.assets_props = {"/b": {"headers": {}}}
    model._system("props_moved", 0)
    assert held.applied_props() == props and model.reindex_pending() == ["indexes"]
    done = False
    for _ in range(20):
        done = reindex_batch()
        if done:
            break
    assert done and model.reindex_pending() == []
    for c, want in ((held, props), (other, {"/b": {"headers": {}}})):
        row = Canister.load(c._id)
        assert row.assets_props == {} and row.applied_props() == want
        assert len(Database.get_instance().load(row._type, row._id)["assets_props"]) < 10
    assert Canister["x4-be"].applied_props() == {} and model.AssetProps.load(Canister["x4-be"]._id) is None
    # a sync that runs before the backfill reaches a row moves its props itself
    late = Canister["x5-be"]
    late.assets_props = props
    late.set_applied_props({**props, "/new": {}})
    assert late.assets_props == {} and late.applied_props() == {**props, "/new": {}}


READ_BUDGET = 250_000  # stored bytes one page may decode: ~3,400 instructions each on the IC (measured on PocketIC), < 1 B


def bytes_read(monkeypatch, run):
    """The stored bytes the storage hands out while ``run`` executes."""
    storage = Database.get_instance()._db_storage
    seen = []
    plain_get, plain_range = storage.get, storage.range
    monkeypatch.setattr(storage, "get", lambda key, *a: seen.append(len(plain_get(key, *a) or "")) or plain_get(key, *a))

    def ranged(*a, **k):
        out = plain_range(*a, **k)
        seen.extend(len(v) for _, v in out)
        return out

    monkeypatch.setattr(storage, "range", ranged)
    run()
    return sum(seen)


def test_a_default_page_of_a_big_orchestra_stays_under_the_read_budget(monkeypatch):
    # 100 canisters, 30 of them frontends with 200 assets each: before AssetProps their rows held ~260 KB each,
    # 7.8 MB for the page, and the staging conductor rejected it past 5 billion instructions.
    world()
    for n in range(100):
        st = Stand(name=f"s{n}")
        st.section = Section["s1"]
        for kind in ("backend", "baton", "frontend") if n < 30 else ("backend",):
            c = Canister(name=f"s{n}-{kind}", canister_id=f"s{n}{kind}-id", kind="frontend" if kind == "frontend" else "backend")
            c.stand = st
            if kind == "frontend":
                c.set_applied_props(props_of(200))
    total = Canister.max_id()
    assert total > 150
    for op in ("orchestra_get", "cycles_get"):
        got = []
        read = bytes_read(monkeypatch, lambda: got.append(call(op)))
        assert len(got[0]["canisters"]) == total
        assert read < READ_BUDGET, f"{op} read {read:,} bytes"
