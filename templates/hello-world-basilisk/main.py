"""Hello-world canister — Basilisk (Python on the IC).

The smallest useful Basilisk canister, plus the one thing a stand's backend
must be able to do in a Casals orchestra: **vote on its own Baton**. The
stand's Baton lists this canister as a commander (`$stand.backend` in the
sheet), so an upgrade the team proposes only runs once this canister approves
it — "the team advises, the user decides". `actions`, `approve` and `reject`
are that vote, driven from the stand's frontend. There is no `propose`:
proposing is the team's side (`casals upgrade --wasm`).

Config (the Baton's id, set by the sheet's `config` item) lives in stable
memory so it survives the upgrades it is there to approve. Build with
`make build-templates`.
"""

import json

from basilisk import (
    Async,
    CallResult,
    Principal,
    Service,
    StableBTreeMap,
    query,
    service_query,
    service_update,
    text,
    update,
)
from ic_python_logging import get_logger

# Opt into Basilisk's built-in introspection endpoints so Casals can drive them
# from the dashboard:
#   __browse__ — read-only data introspection (public @query)
#   __shell__  — run Python inside the canister (controller-only @update)
__basilisk_features__ = ["shell", "browse"]


__version__ = "1.4.0"

# The ic-basilisk-toolkit logger writes to the canister log (ic0.debug_print),
# fetchable via the management canister's fetch_canister_logs / `icp canister
# logs`. Logs are only recorded during replicated execution, so `greet` is an
# @update (a non-replicated @query call would not be logged).
_log = get_logger("hello-world")


@query
def health_check() -> text:
    return '{"status":"ok"}'


@update
def greet(name: text) -> text:
    _log.info("greet called with name=" + name)
    return "Hello, " + name + "! (v" + __version__ + ")"


# ── Config ────────────────────────────────────────────────────────────────────
# The same text-JSON protocol product canisters use, so a sheet's `config`
# items converge against it:
#   {"method": "set_canister_config_json", "args": {"baton": "$stand.baton"},
#    "converged_when": {"query": "get_canister_config_json", "equals_args": true}}
# Stable: an upgrade (which this canister approves through that very Baton)
# must not forget where the Baton is.

_store = StableBTreeMap[text, text](memory_id=1, max_key_size=64, max_value_size=4096)
_CONFIG_KEY = "config"


def _config() -> dict:
    raw = _store.get(_CONFIG_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@update
def set_canister_config_json(args: text) -> text:
    cfg = _config()
    cfg.update(json.loads(args) if args else {})
    _store.insert(_CONFIG_KEY, json.dumps(cfg))
    return json.dumps({"success": True})


@query
def get_canister_config_json() -> text:
    return json.dumps(_config())


# ── Baton client ──────────────────────────────────────────────────────────────
# The Baton speaks text-JSON. This canister is one of its commanders, so calls
# made *from here* carry the vote; the frontend talks to this canister, never
# to the Baton, for anything that changes state.

class Baton(Service):
    @service_query
    def list_actions(self) -> text: ...

    @service_update
    def submit_approval(self, action_id: text) -> text: ...

    @service_update
    def reject_action(self, action_id: text) -> text: ...


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _baton():
    bid = (_config().get("baton") or "").strip()
    if not bid:
        raise Exception("no baton configured: set_canister_config_json {\"baton\": <canister id>}")
    return Baton(Principal.from_str(bid))


def _reply(res: CallResult) -> str:
    """The Baton's own JSON reply, or an error envelope when the call failed."""
    err = getattr(res, "Err", None)
    ok = getattr(res, "Ok", None)
    if ok is None and err is not None:
        return _err(f"baton call failed: {err}")
    return ok if isinstance(ok, str) else json.dumps(ok)


@update
def actions() -> Async[text]:
    """The Baton's action list (`list_actions`), as the Baton returns it.
    An update, not a query: a query cannot make inter-canister calls."""
    try:
        res: CallResult[text] = yield _baton().list_actions()
        return _reply(res)
    except Exception as e:
        return _err(str(e))


@update
def approve(action_id: text) -> Async[text]:
    """Cast this stand's vote for a pending Baton action (`submit_approval`).
    Once the Baton's quorum is met it runs the pipeline on its own."""
    try:
        _log.info("approve action " + action_id)
        res: CallResult[text] = yield _baton().submit_approval(action_id.strip())
        return _reply(res)
    except Exception as e:
        return _err(str(e))


@update
def reject(action_id: text) -> Async[text]:
    """Reject a pending Baton action (`reject_action`)."""
    try:
        _log.info("reject action " + action_id)
        res: CallResult[text] = yield _baton().reject_action(action_id.strip())
        return _reply(res)
    except Exception as e:
        return _err(str(e))
