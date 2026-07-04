"""Unit tests for smoke test helpers."""

from __future__ import annotations

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from smoke import (
    check_smoke_response,
    smoke_call_arg_candid,
    validate_smoke_test,
)


class TestValidateSmokeTest:
    def test_none_ok(self):
        validate_smoke_test(None)

    def test_valid(self):
        validate_smoke_test({"method": "greet"})

    def test_missing_method(self):
        with pytest.raises(ValueError, match="method"):
            validate_smoke_test({})

    def test_blank_method(self):
        with pytest.raises(ValueError, match="method"):
            validate_smoke_test({"method": "  "})


class TestCheckSmokeResponse:
    def test_must_contain_pass(self):
        ok, err = check_smoke_response("Hello, probe!", must_contain="Hello, probe")
        assert ok and not err

    def test_must_contain_fail(self):
        ok, err = check_smoke_response("nope", must_contain="Hello")
        assert not ok and "missing" in err

    def test_must_not_contain_fail(self):
        ok, err = check_smoke_response("Hello error", must_not_contain="error")
        assert not ok and "forbidden" in err

    def test_both_checks(self):
        ok, _ = check_smoke_response("Hello, probe!", must_contain="probe", must_not_contain="fail")
        assert ok


class TestSmokeCallArgCandid:
    def test_empty_arg_unit(self, monkeypatch):
        monkeypatch.setattr(
            "smoke.ic.candid_encode",
            lambda s: b"encoded:" + s.encode(),
        )
        assert smoke_call_arg_candid("") == b"encoded:()"
        assert smoke_call_arg_candid("  ") == b"encoded:()"

    def test_text_arg(self, monkeypatch):
        monkeypatch.setattr(
            "smoke.ic.candid_encode",
            lambda s: s.encode(),
        )
        assert smoke_call_arg_candid('say "hi"') == b'("say \\"hi\\"")'
