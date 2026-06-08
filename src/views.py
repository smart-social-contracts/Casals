"""Serialization helpers — pure, no IC-runtime dependencies.

Converts live domain-model objects (Canister, Stand, Section) into plain
dicts suitable for JSON serialization in query endpoints.  Keeping this
layer pure makes it unit-testable with simple mock objects.
"""

from auth import _normalize_permissions, _parse_permissions
from util import canister_url


def _canister_view(st) -> dict:
    return {
        "name": st.name,
        "canister_id": st.canister_id,
        "kind": st.kind,
        "url": canister_url(st.kind, st.canister_id),
        "wasm_key": st.wasm_key,
        "wasm_hash": st.wasm_hash,
        "status": st.status,
        "snapshot_id": st.snapshot_id,
        "min_cycles": int(st.min_cycles or 0),
        "topup_cycles": int(st.topup_cycles or 0),
        "subnet": st.subnet or "",
    }


def _stand_view(dk) -> dict:
    return {
        "name": dk.name,
        "description": dk.description,
        "commander_principal": dk.commander_principal,
        "permissions": _parse_permissions(dk.permissions),
        "all_permissions": _normalize_permissions(dk.permissions) == "*" or (dk.permissions or "") == "",
        "min_cycles": int(dk.min_cycles or 0),
        "topup_cycles": int(dk.topup_cycles or 0),
        "subnet": dk.subnet or "",
        "subnet_type": dk.subnet_type or "",
        "canisters": [_canister_view(s) for s in (dk.canisters or [])],
    }


def _section_view(sec) -> dict:
    return {
        "name": sec.name,
        "description": sec.description,
        "commander_principal": sec.commander_principal,
        "permissions": _parse_permissions(sec.permissions),
        "all_permissions": _normalize_permissions(sec.permissions) == "*" or (sec.permissions or "") == "",
        "min_cycles": int(sec.min_cycles or 0),
        "topup_cycles": int(sec.topup_cycles or 0),
        "subnet": sec.subnet or "",
        "subnet_type": sec.subnet_type or "",
        "stands": [_stand_view(d) for d in (sec.stands or [])],
    }
