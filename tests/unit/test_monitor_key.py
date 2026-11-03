"""The monitor's read key: made when a monitor is set, rotated on request, dropped with the monitor, and given
only through `monitor_key_get`, to the monitor and to readers of the whole orchestra."""

import fake_chain
from kit import call, grant, world
from model import Event, each, orchestra


def monitor_on(url="https://mon.example"):
    return call("orchestra_set", monitor={"principal": "mon", "url": url})


def test_setting_a_monitor_makes_a_random_key_and_keeps_it_while_the_monitor_stays():
    world()
    monitor_on()
    key = orchestra().monitor_read_key
    assert len(key) == 64 and int(key, 16) >= 0
    monitor_on("https://other.example")
    assert orchestra().monitor_read_key == key
    monitor_on()
    call("orchestra_set", monitor={"principal": "mon2", "url": "https://mon.example"})
    assert orchestra().monitor_read_key != key


def test_a_monitor_set_before_keys_existed_gets_its_first_key_when_the_setting_is_saved_again():
    world()
    orchestra().monitor = {"principal": "mon", "url": "https://mon.example/v1/conductor"}
    assert orchestra().monitor_read_key == ""
    monitor_on()
    assert len(orchestra().monitor_read_key) == 64


def test_rotating_replaces_the_key_and_needs_a_monitor():
    world()
    assert "no monitor" in call("monitor_key_rotate")["error"]
    monitor_on()
    old = orchestra().monitor_read_key
    assert call("monitor_key_rotate")["ok"]
    assert orchestra().monitor_read_key not in ("", old)


def test_rotating_needs_orchestra_settings():
    world()
    monitor_on()
    grant("ann", "stand:d1", "orchestra.settings")
    grant("bob", "orchestra", "canister.deploy")
    assert "needs orchestra.settings" in call("monitor_key_rotate", "ann")["error"]
    assert "needs orchestra.settings" in call("monitor_key_rotate", "bob")["error"]


def test_switching_the_monitor_off_removes_the_key():
    world()
    monitor_on()
    call("orchestra_set", monitor={})
    assert orchestra().monitor_read_key == ""
    assert call("monitor_key_get")["key"] == "" and call("monitor_key_get", "mon")["error"].startswith("unauthorized")


def test_the_monitor_and_readers_of_the_whole_orchestra_read_the_key():
    world()
    monitor_on()
    key = orchestra().monitor_read_key
    grant("all", "orchestra", "canister.browse")
    for who in ("mon", "all", "ctl"):
        assert call("monitor_key_get", who)["key"] == key, who


def test_a_scoped_reader_and_a_stranger_are_refused_the_key():
    world()
    monitor_on()
    grant("part", "stand:d1", "canister.browse")
    for who in ("part", "nobody"):
        assert call("monitor_key_get", who)["error"].startswith("unauthorized"), who


def test_no_view_carries_the_key():
    world()
    monitor_on()
    key = orchestra().monitor_read_key
    grant("all", "orchestra", "canister.browse")
    grant("part", "stand:d1", "canister.browse")
    for who in ("mon", "all", "ctl", "part"):
        assert key not in str(call("orchestra_get", who)), who
    assert key not in str(call("orchestra_export"))


def test_a_new_monitor_cannot_read_the_old_key_when_drawing_the_new_one_fails():
    world()
    monitor_on()
    key = orchestra().monitor_read_key
    fake_chain.state["fail"]["random_bytes"] = "raw_rand failed"
    assert "raw_rand failed" in call("orchestra_set", monitor={"principal": "mon2", "url": "https://mon.example"})["error"]
    assert orchestra().monitor["principal"] == "mon" and orchestra().monitor_read_key == key
    assert call("monitor_key_get", "mon2")["error"].startswith("unauthorized")


def test_the_key_stays_out_of_the_settings_reply_and_the_audit_trail():
    world()
    reply = monitor_on()
    first = orchestra().monitor_read_key
    call("monitor_key_rotate")
    trail = str([e.payload for e in each(Event)])
    assert first not in str(reply) + trail and orchestra().monitor_read_key not in trail


def test_up_makes_the_key_for_a_new_orchestra():
    from test_up import setup, sheet
    setup()
    call("orchestra_up", **sheet(monitor={"principal": "mon", "url": "https://mon.example"}))
    assert len(orchestra().monitor_read_key) == 64
