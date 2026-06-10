"""Native cycles management — the conductor as the orchestra's paymaster.

Casals is the sole controller of every canister it manages, so it can both
observe their balance (canister_status.cycles) and fund them
(deposit_cycles) directly — no external monitor is required. The decision
primitives (``resolve_cycle_policy``, ``decide_topup``, ``cycles_status``)
are pure and unit-tested in ``util.py``; everything here is the on-chain
plumbing around them.

Also contains the fiat-rate (cycles → currency) refresh logic, which shares
the same slow cadence as the balance sampler.
"""

import json

from basilisk import Duration, Principal, ic
from basilisk.canisters.management import management_canister
from basilisk.canisters.xrc import XRC_CANISTER_ID, XRCCanister
from ic_python_logging import get_logger
from models import Canister, CycleSample
from util import cycles_status, decide_topup, resolve_cycle_policy
from audit import _append_event
from helpers import _settings, unwrap_call_result, _nat64s_in

_log = get_logger("casals")

# ── Constants ─────────────────────────────────────────────────────────────────

# The IC Exchange Rate Canister charges 1B cycles per get_exchange_rate call.
XRC_CYCLES_PER_CALL = 1_000_000_000
# Don't pay for a fresh rate more often than this (the sampler timer refreshes
# unconditionally on its own, slower cadence).
FX_MIN_REFRESH_SECS = 300
# Fiat currencies the dashboard offers (XRC FiatCurrency symbols).
FX_SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "CNY", "CAD", "AUD"]

# Cycle-history retention: drop samples older than this, and hard-cap the total
# number of stored samples to bound stable-memory growth.
SAMPLE_RETENTION_SECS = 35 * 24 * 3600   # ~35 days
SAMPLE_MAX = 8000
# Minimum spacing between opportunistic (get_cycles) samples.
SAMPLE_MIN_GAP_SECS = 120

# ── Module-level timer state ──────────────────────────────────────────────────
# These do not survive an upgrade; they are re-armed in post_upgrade via
# _arm_autopilot() / _arm_cycle_sampler().

# Active autopilot timer id (within this instance's lifetime).
_autopilot_timer_id = None
# Active cycle-sampler timer id.
_sampler_timer_id = None
# Last time a balance sample batch was recorded (unix secs); throttles the
# opportunistic sampling done inside get_cycles.
_last_sample_ts = 0

# Volatile cache of the last get_cycles result (JSON string). Populated every
# time get_cycles completes successfully so the frontend can show stale data
# instantly via get_cycles_cached() while the live refresh runs in background.
_cycles_cache: str = ""


# ── Cycle balance helpers ─────────────────────────────────────────────────────

def _status_cycles(status) -> int:
    c = status.get("cycles") if isinstance(status, dict) else getattr(status, "cycles", 0)
    return int(c or 0)


def _status_freezing(status) -> int:
    settings = status.get("settings") if isinstance(status, dict) else getattr(status, "settings", None)
    if settings is None:
        return 0
    fz = settings.get("freezing_threshold") if isinstance(settings, dict) else getattr(settings, "freezing_threshold", 0)
    return int(fz or 0)


def _ic_run_status(status) -> str:
    """Parse IC management canister_status.status to running/stopped/stopping."""
    st = status.get("status") if isinstance(status, dict) else getattr(status, "status", None)
    if st is None:
        return "unknown"
    if isinstance(st, dict):
        for key in ("running", "stopped", "stopping"):
            if key in st:
                return key
    for key in ("running", "stopped", "stopping"):
        if hasattr(st, key):
            return key
    return "unknown"


def _policy_for(st: Canister, s=None):
    """Effective (min_cycles, topup_cycles) for a canister, inheriting up the
    tree."""
    s = s or _settings()
    dk = st.stand
    sec = dk.section if dk else None
    return resolve_cycle_policy(
        canister=(int(st.min_cycles or 0), int(st.topup_cycles or 0)),
        stand=(int(dk.min_cycles or 0), int(dk.topup_cycles or 0)) if dk else (0, 0),
        section=(int(sec.min_cycles or 0), int(sec.topup_cycles or 0)) if sec else (0, 0),
        defaults=(int(s.default_min_cycles or 0), int(s.default_topup_cycles or 0)),
    )


def _resolve_canister_or_stand(params):
    """Return (targets, stand) for a {"canister": ...} or {"stand": ...}
    request."""
    from models import Stand
    if params.get("canister"):
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            raise Exception(f"unknown canister '{params['canister']}'")
        return [st], st.stand
    if params.get("stand"):
        list(Stand.instances())
        dk = Stand[params["stand"].strip()]
        if dk is None:
            raise Exception(f"unknown stand '{params['stand']}'")
        return list(dk.canisters or []), dk
    raise Exception("expected 'canister' or 'stand'")


def _now_secs() -> int:
    return int(ic.time() // 1_000_000_000)


# ── Cycle history recording ───────────────────────────────────────────────────

def _record_cycle_sample(st: Canister, ts: int, cycles: int) -> None:
    """Append one balance reading for a canister (denormalized with its
    position in the tree so history survives restructuring)."""
    dk = st.stand
    sec = dk.section if dk else None
    smp = CycleSample(
        canister_id=st.canister_id,
        canister_name=st.name,
        stand_name=dk.name if dk else "",
        section_name=sec.name if sec else "",
        kind=st.kind,
    )
    smp.ts = int(ts)
    smp.cycles = int(cycles)
    smp.deposited = int(st.cycles_deposited or 0)


def _prune_cycle_samples(now: int) -> None:
    """Bound stable-memory growth: drop samples past the retention window,
    then, if still over the hard cap, drop the oldest until under it."""
    try:
        samples = list(CycleSample.instances())
        cutoff = now - SAMPLE_RETENTION_SECS
        stale = [s for s in samples if int(s.ts or 0) < cutoff]
        for s in stale:
            s.delete()
        remaining = [s for s in samples if int(s.ts or 0) >= cutoff]
        overflow = len(remaining) - SAMPLE_MAX
        if overflow > 0:
            remaining.sort(key=lambda x: int(x.ts or 0))
            for s in remaining[:overflow]:
                s.delete()
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"prune cycle samples failed: {e}")


# ── Sampler timer ─────────────────────────────────────────────────────────────

def _sample_all_gen(ts: int):
    """Generator: read every canister's balance and record a sample (no
    top-ups)."""
    list(Canister.instances())
    n = 0
    for st in Canister.instances():
        if not st.canister_id:
            continue
        try:
            status_res = yield management_canister.canister_status(
                {"canister_id": Principal.from_str(st.canister_id)}
            )
            status = unwrap_call_result(status_res)
            _record_cycle_sample(st, ts, _status_cycles(status))
            n += 1
        except Exception as e:  # pragma: no cover - per-canister, keep going
            _log.error(f"sample {st.name} failed: {e}")
    if n:
        _prune_cycle_samples(ts)
        global _last_sample_ts
        _last_sample_ts = ts
    return n


def _sampler_cb():
    """Cycle-sampler timer callback (generator; never raises). Also refreshes
    the fiat conversion factor on the same (slow) cadence."""
    try:
        n = yield from _sample_all_gen(_now_secs())
        _log.info(f"cycle sampler: recorded {n} samples")
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"cycle sampler failed: {e}")
    try:
        yield from _refresh_fx_gen()
    except Exception as e:  # pragma: no cover - best-effort
        _log.error(f"fx refresh failed: {e}")


def _arm_cycle_sampler() -> None:
    """(Re)arm the balance-sampling timer to match current settings.
    Independent of autopilot; re-armed on init/post_upgrade since timers
    don't survive upgrades."""
    global _sampler_timer_id
    try:
        if _sampler_timer_id is not None:
            try:
                ic.clear_timer(_sampler_timer_id)
            except Exception:
                pass
            _sampler_timer_id = None
        s = _settings()
        interval = int(s.cycles_sample_interval_secs or 0)
        if s.cycles_sampling and interval > 0:
            _sampler_timer_id = ic.set_timer_interval(Duration(interval), _sampler_cb)
            _log.info(f"cycle sampler armed: every {interval}s")
    except Exception as e:  # pragma: no cover - defensive at install time
        _log.error(f"could not arm cycle sampler: {e}")


# ── Autopilot reconcile timer ─────────────────────────────────────────────────

def _reconcile_all_gen():
    """Generator: top up every canister below its policy threshold.

    Returns a summary dict {treasury, topped_up, checked, results}. Used both
    by the ``reconcile`` endpoint and by the autopilot timer.
    """
    list(Canister.instances())
    s = _settings()
    reserve = int(s.treasury_reserve or 0)
    treasury = int(ic.canister_balance128())
    batch_ts = _now_secs()
    results = []
    topped = 0
    sampled = False
    for st in Canister.instances():
        if not st.canister_id:
            continue
        try:
            status_res = yield management_canister.canister_status(
                {"canister_id": Principal.from_str(st.canister_id)}
            )
            status = unwrap_call_result(status_res)
            bal = _status_cycles(status)
            frz = _status_freezing(status)
        except Exception as e:
            results.append({"canister": st.name, "canister_id": st.canister_id, "error": str(e)})
            continue
        min_c, topup_c = _policy_for(st, s)
        amount = decide_topup(bal, frz, min_c, topup_c, treasury, reserve)
        if amount > 0:
            try:
                yield management_canister.deposit_cycles(
                    {"canister_id": Principal.from_str(st.canister_id)}
                ).with_cycles(amount)
                treasury -= amount
                topped += 1
                st.cycles_deposited = int(st.cycles_deposited or 0) + amount
                _append_event("cycles_topup", st.canister_id,
                              {"amount": amount, "balance_before": bal})
                results.append({"canister": st.name, "topped_up": amount, "balance_before": bal})
                bal = bal + amount
            except Exception as e:
                results.append({"canister": st.name, "canister_id": st.canister_id, "error": str(e)})
        else:
            label = cycles_status(bal, frz, min_c)
            wanted = bool(min_c > 0 and topup_c > 0 and (bal - frz) < min_c)
            if wanted:
                _append_event("cycles_low", st.canister_id,
                              {"balance": bal, "status": label, "reason": "treasury exhausted"})
            else:
                _append_event("cycles_checked", st.canister_id,
                              {"balance": bal, "status": label})
            results.append({"canister": st.name, "balance": bal, "status": label})
        _record_cycle_sample(st, batch_ts, bal)
        sampled = True
    if sampled:
        _prune_cycle_samples(batch_ts)
        global _last_sample_ts
        _last_sample_ts = batch_ts
    return {"treasury": treasury, "topped_up": topped, "checked": len(results), "results": results}


def _reconcile_cb():
    """Autopilot timer callback (generator; never raises)."""
    try:
        summary = yield from _reconcile_all_gen()
        _append_event("cycles_reconcile", "", {
            "checked": summary.get("checked", 0),
            "topped_up": summary.get("topped_up", 0),
            "source": "autopilot",
        })
        _log.info(f"autopilot reconcile: {summary.get('topped_up')} topped of {summary.get('checked')}")
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"autopilot reconcile failed: {e}")


def _arm_autopilot() -> None:
    """(Re)arm the recurring reconcile timer to match current settings.

    IC timers do not survive upgrades, so this is also called from
    init / post_upgrade. Clears any timer armed earlier in this instance's
    lifetime before setting a new one, so toggling settings never stacks
    timers.
    """
    global _autopilot_timer_id
    try:
        if _autopilot_timer_id is not None:
            try:
                ic.clear_timer(_autopilot_timer_id)
            except Exception:
                pass
            _autopilot_timer_id = None
        s = _settings()
        interval = int(s.cycles_check_interval_secs or 0)
        if s.cycles_autopilot and interval > 0:
            _autopilot_timer_id = ic.set_timer_interval(Duration(interval), _reconcile_cb)
            _log.info(f"autopilot armed: every {interval}s")
    except Exception as e:  # pragma: no cover - defensive at install time
        _log.error(f"could not arm autopilot: {e}")


# ── Fiat-rate (cycles → currency) helpers ─────────────────────────────────────

def _fetch_icp_rate_gen(currency: str):
    """Generator: the ICP price in ``currency`` (float), via the XRC."""
    xrc = XRCCanister(Principal.from_str(XRC_CANISTER_ID))
    res = yield xrc.get_exchange_rate({
        "base_asset": {"symbol": "ICP", "class": {"Cryptocurrency": None}},
        "quote_asset": {"symbol": currency, "class": {"FiatCurrency": None}},
        "timestamp": None,
    }).with_cycles(XRC_CYCLES_PER_CALL)
    gr = unwrap_call_result(res)
    ok = gr.get("Ok") if isinstance(gr, dict) else getattr(gr, "Ok", None)
    if ok is None:
        err = gr.get("Err") if isinstance(gr, dict) else getattr(gr, "Err", None)
        raise Exception(f"XRC: {err}")
    rate = ok.get("rate") if isinstance(ok, dict) else getattr(ok, "rate")
    meta = ok.get("metadata") if isinstance(ok, dict) else getattr(ok, "metadata")
    decimals = meta.get("decimals") if isinstance(meta, dict) else getattr(meta, "decimals")
    return float(rate) / float(10 ** int(decimals))


_CMC_CANISTER_ID = "rkp4c-7iaaa-aaaaa-aaaca-cai"


def _fetch_xdr_permyriad_per_icp_gen():
    """Generator: the CMC's xdr_permyriad_per_icp (int, XDR*1e4 per ICP)."""
    res = yield ic.call_raw(
        Principal.from_str(_CMC_CANISTER_ID), "get_icp_xdr_conversion_rate",
        ic.candid_encode("()"), 0)
    decoded = ic.candid_decode(unwrap_call_result(res))
    vals = _nat64s_in(decoded)
    if not vals:
        raise Exception(f"CMC rate: no nat64 in {decoded[:200]}")
    return min(vals)


def _refresh_fx_gen():
    """Generator: refresh and persist the cycles→currency factor. Returns the
    currency value of 1T cycles (float). Records errors on the Settings row."""
    s = _settings()
    currency = ((s.display_currency or "USD").strip().upper()) or "USD"
    try:
        icp_in_cur = yield from _fetch_icp_rate_gen(currency)
        permyriad = yield from _fetch_xdr_permyriad_per_icp_gen()
        xdr_per_icp = float(permyriad) / 10000.0
        if xdr_per_icp <= 0 or icp_in_cur <= 0:
            raise Exception("non-positive rate")
        currency_per_tcycle = icp_in_cur / xdr_per_icp
        s.fx_micro_per_tcycle = int(round(currency_per_tcycle * 1_000_000))
        s.fx_currency = currency
        s.fx_updated = _now_secs()
        s.fx_error = ""
        return currency_per_tcycle
    except Exception as e:
        s.fx_error = str(e)[:255]
        s.fx_updated = _now_secs()
        raise
