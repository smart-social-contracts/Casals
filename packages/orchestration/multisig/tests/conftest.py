"""Multisig integration tests reuse Baton replica helpers."""

import importlib.util
import os

import pytest

BATON_TESTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "baton", "tests"))
BATON_CONFTEST = os.path.join(BATON_TESTS, "conftest.py")

_spec = importlib.util.spec_from_file_location("baton_test_conftest", BATON_CONFTEST)
_baton_conftest = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_baton_conftest)

MULTISIG_ROOT = _baton_conftest.MULTISIG_ROOT
build_multisig = _baton_conftest.build_multisig
call = _baton_conftest.call
icp = _baton_conftest.icp
identity_principal = _baton_conftest.identity_principal
install_baton = _baton_conftest.install_baton
install_multisig = _baton_conftest.install_multisig
parse_nat_output = _baton_conftest.parse_nat_output
ensure_identity = _baton_conftest.ensure_identity


@pytest.fixture(scope="session")
def replica():
    _baton_conftest.ensure_replica()
    _baton_conftest.build_managed_wasms()
    yield
