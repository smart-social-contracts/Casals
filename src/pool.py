"""Canister pool — reuse before create.

Creating a canister is expensive (cycles + latency), so Casals never
discards one.  When a canister is retired it is stopped and returned to the
pool as ``free``; a later deployment that needs a new canister reuses a
free entry (reinstalling fresh code) before paying to create another.
"""

from ic_python_logging import get_logger
from models import Canister, PooledCanister

_log = get_logger("casals")


def _pool_take_free(subnet: str = "", subnet_type: str = "") -> str:
    """Return the id of a free pooled canister matching the desired placement,
    or '' if none is available.

      - explicit ``subnet``: only a free canister recorded on that subnet;
      - ``subnet_type``: only a free canister created for that type;
      - neither: any free canister (default placement).

    A constrained target never reuses a canister of unknown placement — we'd
    risk landing on the wrong subnet — so it creates a fresh one instead.

    Safety: never hand out a canister that already backs a live Canister
    record (a stale ``free`` pool entry left by a partial deploy must not
    overwrite a running service).
    """
    list(PooledCanister.instances())
    list(Canister.instances())
    live_cids = {st.canister_id for st in Canister.instances() if st.canister_id}
    for p in PooledCanister.instances():
        if p.status != "free" or not p.canister_id:
            continue
        if p.canister_id in live_cids:
            p.status = "in_use"
            p.canister_name = "(recovered)"
            _log.error(f"pool_take_free: skipped live canister {p.canister_id}; re-marked in_use")
            continue
        if subnet:
            if p.subnet == subnet:
                return p.canister_id
        elif subnet_type:
            if p.subnet_type == subnet_type:
                return p.canister_id
        else:
            return p.canister_id
    return ""


def _pool_register(canister_id: str, subnet: str = "", subnet_type: str = "") -> "PooledCanister":
    """Ensure a PooledCanister exists for ``canister_id``, recording its
    (known) subnet placement. Returns the record."""
    list(PooledCanister.instances())
    p = PooledCanister[canister_id]
    if p is None:
        p = PooledCanister(canister_id=canister_id)
    if subnet:
        p.subnet = subnet
    if subnet_type:
        p.subnet_type = subnet_type
    return p


def _pool_mark_in_use(canister_id: str, canister_name: str) -> None:
    p = _pool_register(canister_id)
    p.status = "in_use"
    p.canister_name = canister_name


def _pool_free(canister_id: str) -> None:
    # Preserve the recorded subnet placement; only clear the occupancy.
    p = _pool_register(canister_id)
    p.status = "free"
    p.canister_name = ""
