import json

import pytest

import op
from kit import call, world
from model import Event, each


def test_missing_arg_and_event():
    world()
    assert call("grant_set", scope="stand:d1") == {"ok": False, "error": "missing 'principal'"}
    call("grant_set", scope="stand:d1", principal="ann", permissions=[])
    [ev] = list(each(Event))
    assert (ev.type, ev.caller, ev.payload["principal"]) == ("grant_set", "ctl", "ann")


def test_large_payload_is_reduced_to_keys():
    world()
    call("grant_set", scope="stand:d1", principal="ann", note="x" * 3000)
    assert list(each(Event))[0].payload == {"keys": ["note", "principal", "scope"]}


def test_reads_are_not_audited_and_bad_json_fails():
    world()
    call("grant_list")
    assert list(each(Event)) == []
    assert json.loads(op.run(op.dispatch(op.OPS["grant_list"]["fn"], "{")))["ok"] is False


def test_query_cannot_yield():
    def gen():
        yield "call"

    with pytest.raises(RuntimeError):
        op.run(gen())
