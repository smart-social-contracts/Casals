"""Hello-world canister — Basilisk (Python on the IC).

The smallest useful Basilisk canister: a single call that greets a name and
logs it. Used as a Casals catalog template to demonstrate creating a
Python-runtime stand. Build with `make build-templates`.
"""

from basilisk import text, update
from ic_python_logging import get_logger

# Opt into Basilisk's built-in introspection endpoints so Casals can drive them
# from the dashboard:
#   __browse__ — read-only data introspection (public @query)
#   __shell__  — run Python inside the canister (controller-only @update)
__basilisk_features__ = ["shell", "browse"]


__version__ = "1.2.0"

# The ic-basilisk-toolkit logger writes to the canister log (ic0.debug_print),
# fetchable via the management canister's fetch_canister_logs / `icp canister
# logs`. Logs are only recorded during replicated execution, so `greet` is an
# @update (a non-replicated @query call would not be logged).
_log = get_logger("hello-world")


@update
def greet(name: text) -> text:
    _log.info("greet called with name=" + name)
    return "Hello, " + name + "! (v" + __version__ + ")"
