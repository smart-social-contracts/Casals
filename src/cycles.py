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
        minted = yield from _treasury_watch_begin_gen()
        yield from _treasury_watch_end_gen(minted_cycles=minted, spent_cycles=0)
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
    minted = yield from _treasury_watch_begin_gen()
    reserve = int(s.treasury_reserve or 0)
    treasury = int(ic.canister_balance128())
    batch_ts = _now_secs()
    results = []
    topped = 0
    topped_total = 0
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
                topped_total += amount
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
    yield from _treasury_watch_end_gen(minted_cycles=minted, spent_cycles=topped_total)
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


# The NNS ICP ledger canister (mainnet).
ICP_LEDGER_CANISTER_ID = "ryjl3-tyaaa-aaaaa-aaaba-cai"
# Ledger memo for ICP→cycles conversion via the CMC (see ic-ledger-types).
MEMO_TOP_UP_CANISTER = 1_347_768_404
ICP_TRANSFER_FEE_E8S = 10_000
# Ignore balance jitter below these thresholds when detecting deposits.
ICP_DEPOSIT_DUST_E8S = 1_000
CYCLES_DEPOSIT_DUST = 10_000_000  # 10M cycles


def icp_convert_amount(icp_e8s: int, fee: int = ICP_TRANSFER_FEE_E8S):
    """Return the ICP transfer amount (e8s) if balance is convertible, else None."""
    if icp_e8s is None or icp_e8s <= fee:
        return None
    return int(icp_e8s) - fee


def icp_autoconvert_enabled(s=None) -> bool:
    """True unless a Settings row explicitly disables ICP auto-convert."""
    s = s or _settings()
    raw = getattr(s, "cycles_icp_autoconvert", None)
    if raw is None:
        return True
    return bool(int(raw))


def treasury_cycles_deposit_amount(
    cycles_now: int,
    prev_cycles: int,
    minted: int = 0,
    spent: int = 0,
    dust: int = CYCLES_DEPOSIT_DUST,
) -> int:
    """Return external cycles deposited since last observation, or 0."""
    expected = int(prev_cycles) + int(minted or 0) - int(spent or 0)
    delta = int(cycles_now) - expected
    return delta if delta > int(dust) else 0


def _treasury_ledger_account_hex() -> str:
    """64-char hex ledger account ID for this canister's default ICP account."""
    return _ledger_account_bytes(ic.id().to_account_id()).hex()


def _sync_treasury_baseline(cycles=None, icp_e8s=None) -> None:
    """Refresh stored baselines after an internal treasury change (e.g. destroy)."""
    s = _settings()
    if cycles is not None:
        s.treasury_last_cycles = int(cycles)
    if icp_e8s is not None:
        s.treasury_last_icp_e8s = int(icp_e8s)
    s.treasury_watch_initialized = 1


def _treasury_watch_begin_gen(force_convert: bool = False):
    """Detect ICP deposits and optionally convert ledger ICP to cycles.

    Returns minted cycles (int) from any conversion performed in this step.
    """
    s = _settings()
    prev_icp = int(s.treasury_last_icp_e8s or 0)
    initialized = bool(int(s.treasury_watch_initialized or 0))

    icp_before = yield from _treasury_icp_e8s_gen()
    if icp_before is not None:
        icp_before = int(icp_before)
        if initialized and icp_before > prev_icp + ICP_DEPOSIT_DUST_E8S:
            _append_event("treasury_icp_deposit", "", {
                "amount_e8s": icp_before - prev_icp,
                "balance_e8s": icp_before,
            })

    convert = yield from _maybe_convert_icp_to_cycles_gen(force=force_convert)
    if convert.get("converted"):
        return int(convert.get("cycles") or 0)
    return 0


def _treasury_watch_end_gen(minted_cycles: int = 0, spent_cycles: int = 0):
    """Detect cycles deposits and persist the latest treasury baselines."""
    s = _settings()
    prev_cycles = int(s.treasury_last_cycles or 0)
    initialized = bool(int(s.treasury_watch_initialized or 0))

    cycles_now = int(ic.canister_balance128())
    icp_now = yield from _treasury_icp_e8s_gen()
    icp_now = int(icp_now or 0) if icp_now is not None else int(s.treasury_last_icp_e8s or 0)

    if initialized:
        amount = treasury_cycles_deposit_amount(
            cycles_now, prev_cycles, minted=minted_cycles, spent=spent_cycles,
        )
        if amount > 0:
            _append_event("treasury_cycles_deposit", "", {
                "amount": amount,
                "balance": cycles_now,
            })
    else:
        initialized = True

    s.treasury_last_cycles = cycles_now
    s.treasury_last_icp_e8s = icp_now
    s.treasury_watch_initialized = 1


def _subaccount_from_principal(principal) -> bytes:
    """CMC subaccount encoding: principal bytes right-aligned in 32 bytes."""
    raw = getattr(principal, "_bytes", None)
    if raw is None:
        raw = bytes(principal)
    if len(raw) > 32:
        raise ValueError("principal too long for subaccount")
    sub = bytearray(32)
    sub[32 - len(raw):] = raw
    return bytes(sub)


def _variant_ok_first_number(decoded: str):
    """Extract the first numeric value from a Candid Ok variant."""
    marker = "Ok = "
    j = decoded.find(marker)
    if j < 0:
        return None
    rest = decoded[j + len(marker):]
    end = 0
    while end < len(rest) and (rest[end].isdigit() or rest[end] == "_"):
        end += 1
    num = rest[:end].replace("_", "")
    return int(num) if num else None

def _ledger_account_bytes(account) -> bytes:
    """Raw 32-byte ledger AccountIdentifier from a Principal.to_account_id() value."""
    raw = getattr(account, "_bytes", None)
    if raw is None:
        raw = getattr(account, "bytes", None)
        if callable(raw):
            raw = raw()
    if raw is None:
        s = account.to_str()
        raw = bytes.fromhex(s[2:] if s.startswith("0x") else s)
    return bytes(raw)


def _treasury_icp_e8s_gen():
    """Generator: ICP ledger balance (e8s) held in this canister's default account."""
    try:
        acct_hex = _ledger_account_bytes(ic.id().to_account_id()).hex()
        arg = ic.candid_encode(f'(record {{ account = blob "{acct_hex}" }})')
        res = yield ic.call_raw(
            Principal.from_str(ICP_LEDGER_CANISTER_ID),
            "account_balance",
            arg,
            0,
        )
        decoded = ic.candid_decode(unwrap_call_result(res))
        vals = _nat64s_in(decoded)
        if not vals:
            raise Exception(f"unexpected ledger reply: {decoded[:200]}")
        return int(vals[0])
    except Exception as e:  # pragma: no cover - ledger absent on some replicas
        _log.error(f"treasury icp balance: {e}")
        return None


_CMC_CANISTER_ID = "rkp4c-7iaaa-aaaaa-aaaca-cai"


def _maybe_convert_icp_to_cycles_gen(force: bool = False):
    """Generator: burn ledger ICP into cycles on this canister via the CMC.

    Flow: ICP ledger transfer (memo=top-up) to the CMC account keyed by this
    canister's principal, then ``notify_top_up`` to mint cycles here.
    """
    s = _settings()
    if not force and not icp_autoconvert_enabled(s):
        return {"converted": False, "reason": "disabled"}

    icp_e8s = yield from _treasury_icp_e8s_gen()
    if icp_e8s is None:
        return {"converted": False, "reason": "ledger unavailable"}

    amount_e8s = icp_convert_amount(icp_e8s)
    if amount_e8s is None:
        return {"converted": False, "icp_e8s": icp_e8s, "reason": "below minimum"}

    try:
        canister_id = ic.id()
        cid_str = str(canister_id)
        sub = _subaccount_from_principal(canister_id)
        to_acct = Principal.from_str(_CMC_CANISTER_ID).to_account_id(subaccount=sub)
        to_hex = _ledger_account_bytes(to_acct).hex()
        transfer_arg = (
            f"(record {{ memo = {MEMO_TOP_UP_CANISTER} : nat64; "
            f"amount = record {{ e8s = {amount_e8s} : nat64 }}; "
            f"fee = record {{ e8s = {ICP_TRANSFER_FEE_E8S} : nat64 }}; "
            f"from_subaccount = null; to = blob \"{to_hex}\"; created_at_time = null }})"
        )
        res = yield ic.call_raw(
            Principal.from_str(ICP_LEDGER_CANISTER_ID),
            "transfer",
            ic.candid_encode(transfer_arg),
            0,
        )
        decoded = ic.candid_decode(unwrap_call_result(res))
        block_index = _variant_ok_first_number(decoded)
        if block_index is None:
            err = decoded[:300]
            _log.error(f"icp transfer for mint failed: {err}")
            return {"converted": False, "icp_e8s": icp_e8s, "error": err}

        notify_arg = (
            f"(record {{ block_index = {block_index} : nat64; "
            f"canister_id = principal \"{cid_str}\" }})"
        )
        res2 = yield ic.call_raw(
            Principal.from_str(_CMC_CANISTER_ID),
            "notify_top_up",
            ic.candid_encode(notify_arg),
            0,
        )
        decoded2 = ic.candid_decode(unwrap_call_result(res2))
        cycles = _variant_ok_first_number(decoded2)
        if cycles is None:
            err = decoded2[:300]
            _log.error(f"notify_top_up failed: {err}")
            return {"converted": False, "block_index": block_index, "error": err}

        _append_event("cycles_icp_convert", "", {
            "icp_e8s": amount_e8s,
            "fee_e8s": ICP_TRANSFER_FEE_E8S,
            "cycles": cycles,
            "block_index": block_index,
            "source": "manual" if force else "autoconvert",
        })
        _log.info(f"converted {amount_e8s} e8s ICP -> {cycles} cycles")
        return {
            "converted": True,
            "icp_e8s": amount_e8s,
            "cycles": cycles,
            "block_index": block_index,
        }
    except Exception as e:  # pragma: no cover - on-chain failures
        _log.error(f"icp autoconvert: {e}")
        return {"converted": False, "error": str(e)[:255]}


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
