from fake_chain import ANONYMOUS
from grants import checksum
from kit import call, grant, world
from model import Event, rows


def test_controller_sets_and_lists_a_grant():
    world()
    assert call("grant_set", scope="stand:d1", principal="ann", permissions=["canister.deploy"])["ok"]
    assert call("grant_list")["grants"] == [
        {"scope": "stand:d1", "principal": "ann", "permissions": ["canister.deploy"], "calls": []}]


def test_unknown_permission_and_scope():
    world()
    assert "unknown permissions: nope" in call("grant_set", scope="stand:d1", principal="ann", permissions=["nope"])["error"]
    assert "unknown stand 'x'" in call("grant_set", scope="stand:x", principal="ann")["error"]
    assert "unknown scope" in call("grant_set", scope="realm:x", principal="ann")["error"]


def test_bounded_delegation():
    world()
    grant("lead", "section:s1", "grant.assign", "canister.deploy")
    grant("boss", "stand:d1", "*")
    ok = call("grant_set", "lead", scope="stand:d1", principal="ann", permissions=["canister.deploy"])
    assert ok["ok"]
    for perms in (["canister.create"], ["*"]):
        assert "exceed" in call("grant_set", "lead", scope="stand:d1", principal="ann", permissions=perms)["error"]
    assert "own grant" in call("grant_set", "lead", scope="section:s1", principal="lead")["error"]
    assert "exceed" in call("grant_remove", "lead", scope="stand:d1", principal="boss")["error"]
    assert call("grant_remove", "lead", scope="stand:d1", principal="ann")["ok"]
    assert "no grant" in call("grant_remove", scope="stand:d1", principal="ann")["error"]


def test_calls_are_bounded_by_the_callers_calls():
    world()
    mine = {"canister_id": "c1-id", "method": "ping"}
    grant("lead", "section:s1", "grant.assign", calls=[mine])
    other = {"canister_id": "c1-id", "method": "drop"}
    assert "calls you do not hold" in call("grant_set", "lead", scope="stand:d1", principal="ann", calls=[other])["error"]
    assert call("grant_set", "lead", scope="stand:d1", principal="ann", calls=[mine])["ok"]


def test_access_code_slot_is_hidden_then_claimed_once():
    world()
    slot = checksum("open sesame")
    call("grant_set", scope="section:s1", principal=slot, permissions=["stand.create"])
    grant("ann", "section:s1", "canister.deploy")
    grant("lead", "orchestra", "grant.assign")
    assert {g["principal"] for g in call("grant_list", "ann")["grants"]} == {"ann", "sha256:hidden"}
    assert slot in {g["principal"] for g in call("grant_list", "lead")["grants"]}
    assert call("grant_claim", "ann", code=" open sesame ")["claimed"] == ["section:s1"]
    assert call("grant_list", "ann")["grants"][0]["permissions"] == ["canister.deploy", "stand.create"]
    assert "already used" in call("grant_claim", "bob", code="open sesame")["error"]
    assert all("open sesame" not in str(e.payload) for e in rows(Event))


def test_claims_are_rate_limited():
    for _ in range(5):
        assert "invalid" in call("grant_claim", "bob", code="guess")["error"]
    assert "too many" in call("grant_claim", "bob", code="guess")["error"]


def test_failure_memory_is_bounded():
    import grants

    grants.failures.update({str(i): [] for i in range(2001)})
    call("grant_claim", "bob", code="guess")
    assert list(grants.failures) == ["bob"]


def test_scoped_reader_sees_only_its_scope():
    world()
    grant("ann", "section:s2", "*")
    grant("bob", "stand:d1", "canister.deploy")
    assert [g["principal"] for g in call("grant_list", "ann")["grants"]] == ["ann"]
    assert "not a reader" in call("grant_list", "zed")["error"]
    assert "sign in" in call("grant_claim", ANONYMOUS, code="x")["error"]
