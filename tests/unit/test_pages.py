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


def test_export_pages_merge_by_section():
    many()
    whole = call("orchestra_export")
    split = call("orchestra_export", count=2, sections_from=None, stands_from=3)
    assert split["sections"] and all(set(sec) == {"name", "stands"} for sec in split["sections"])
    out = pages("orchestra_export", count=2)
    for key in ("wasms", "grants", "sections"):
        assert out[key] == whole[key], key


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
