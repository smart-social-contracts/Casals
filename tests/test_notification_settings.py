"""Notification addresses (unsubscribe, admin list/remove) and the settings
fields conductor commanders may change. Real entity layer, in-memory store."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ic_python_db.db_engine import Database  # noqa: E402
from ic_python_db.storage import MemoryStorage  # noqa: E402

SELF = "zzzzz-zz"
ALICE = "alice-principal"
BOB = "bob-principal"


class _P:
    def __init__(self, v):
        self.v = v

    def to_str(self):
        return self.v


class _FakeIC:
    who = ALICE

    @staticmethod
    def id():
        return _P(SELF)

    @classmethod
    def caller(cls):
        return _P(cls.who)

    @staticmethod
    def time():
        return 1_700_000_000 * 1_000_000_000


@pytest.fixture
def main(monkeypatch):
    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import main
    import audit
    import helpers
    for mod in (audit, helpers, main):
        monkeypatch.setattr(mod, "ic", _FakeIC)
    _FakeIC.who = ALICE
    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_conductor_commander_can", lambda perm: False)
    yield main
    Database._instance = prev


def _saved(main, principal, email, verified=False):
    from models import UserSettings
    row = UserSettings(principal=principal)
    row.notification_email = email
    row.notification_email_verified = verified
    return row


def _call(fn, args=None):
    return json.loads(fn(json.dumps(args)) if args is not None else fn())


def test_unsubscribe_stops_every_row_with_that_address_until_saved_again(main, monkeypatch):
    _saved(main, ALICE, "a@example.test", verified=True)
    _saved(main, BOB, "a@example.test")
    _saved(main, "carol-principal", "c@example.test")

    assert _call(main.unsubscribe_notification_email, {"email": "a@example.test"})["ok"] is False
    monkeypatch.setattr(main, "_is_monitor_caller", lambda: True)
    res = _call(main.unsubscribe_notification_email, {"email": "a@example.test", "reason": "not_me"})
    assert res["ok"] and sorted(res["principals"]) == [ALICE, BOB]

    assert main._user_notification_emails() == []
    assert main._notification_email_pending() == [{"principal": "carol-principal", "email": "c@example.test"}]
    by_principal = {e["principal"]: e for e in main._notification_email_entries()}
    assert by_principal[ALICE]["unsubscribed"] and not by_principal["carol-principal"]["unsubscribed"]
    refused = _call(main.confirm_notification_email, {"principal": ALICE, "email": "a@example.test"})
    assert refused["ok"] is False and "stopped notices" in refused["error"]

    mine = _call(main.get_my_settings)
    assert mine["notification_email_status"] == "declined" and mine["notification_email_unsubscribed_at"] > 0

    monkeypatch.setattr(main, "_require_any_commander", lambda: None)
    saved = _call(main.set_my_settings, {"notification_email": "a@example.test"})
    assert saved["notification_email_status"] == "pending"
    assert _call(main.confirm_notification_email, {"principal": ALICE, "email": "a@example.test"})["ok"]


def test_recipients_list_and_remove_need_notification_manage(main, monkeypatch):
    _saved(main, ALICE, "alice@example.test", verified=True)
    _saved(main, BOB, "bob@example.test")
    main._settings().alert_emails = "ops@example.test"

    assert _call(main.list_notification_recipients)["ok"] is False
    assert _call(main.remove_notification_email, {"principal": BOB})["ok"] is False

    monkeypatch.setattr(main, "_conductor_commander_can", lambda perm: perm == "notification.manage")
    listed = _call(main.list_notification_recipients)
    assert listed["legacy_email"] == "o***@example.test"
    assert {(r["principal"], r["email"], r["status"]) for r in listed["recipients"]} == {
        (ALICE, "a***@example.test", "confirmed"), (BOB, "b***@example.test", "pending"),
    }

    assert _call(main.remove_notification_email, {"principal": BOB})["removed"] == BOB
    assert _call(main.remove_notification_email, {"legacy": True})["removed"] == "legacy"
    listed = _call(main.list_notification_recipients)
    assert [r["principal"] for r in listed["recipients"]] == [ALICE] and listed["legacy_email"] == ""


def test_commanders_change_only_the_settings_their_permissions_cover(main, monkeypatch):
    held = {"settings.cycles"}
    monkeypatch.setattr(main, "_conductor_commander_can", lambda perm: perm in held)
    monkeypatch.setattr(main, "refresh_cycles_snapshot_settings", lambda: None)

    assert _call(main.set_settings, {"treasury_reserve": 7})["ok"]
    assert main._settings().treasury_reserve == 7

    res = _call(main.set_settings, {"treasury_reserve": 9, "orchestra_name": "x"})
    assert res["ok"] is False and "settings.general" in res["error"]
    assert main._settings().treasury_reserve == 7, "a refused call changes nothing"

    res = _call(main.set_settings, {"open_access": True})
    assert res["ok"] is False and "Casals controller" in res["error"]

    assert _call(main.get_my_settings)["editable_settings"] == {
        "controller": False, "general": False, "cycles": True, "monitor": False, "notifications": False,
    }
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    assert _call(main.set_settings, {"open_access": True})["ok"]
    assert all(_call(main.get_my_settings)["editable_settings"].values())
