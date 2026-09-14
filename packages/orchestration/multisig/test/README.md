# Multisig test fixtures

Integration tests for `ApplySheet` need a Casals (or mock) canister whose
`apply(text)` returns a scripted JSON sequence. Deploy a stub here when running
`pytest tests/test_integration.py` against a local replica.

Unit tests in `tests/test_unit.py` mirror the Motoko apply loop with
`FakeCasalsApply` and do not require a replica.
