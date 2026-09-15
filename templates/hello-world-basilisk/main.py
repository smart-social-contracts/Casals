"""Hello-world canister — Basilisk (Python on the IC).

The smallest useful Basilisk canister: a single call that greets a name and
logs it. Used as a Casals catalog template to demonstrate creating a
Python-runtime stand. Build with `make build-templates`.
"""

import json

from basilisk import query, text, update
from ic_python_logging import get_logger

# Opt into Basilisk's built-in introspection endpoints so Casals can drive them
# from the dashboard:
#   __browse__ — read-only data introspection (public @query)
#   __shell__  — run Python inside the canister (controller-only @update)
__basilisk_features__ = ["shell", "browse"]


__version__ = "1.3.0"

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


# A tiny configurable surface, the same text-JSON protocol product canisters
# use, so a sheet's `config` items can be exercised against this template.
_config: dict = {}


@update
def set_canister_config_json(args: text) -> text:
    _config.update(json.loads(args) if args else {})
    return json.dumps({"success": True})


@query
def get_canister_config_json() -> text:
    return json.dumps(_config)
