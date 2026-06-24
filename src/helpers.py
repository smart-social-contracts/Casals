"""Small IC-runtime helpers — the shared primitives used throughout the
codebase.

Centralising these here avoids the circular-import problem: submodules
(audit, pool, sheet, lifecycle, cycles) import from here; main.py imports
from the submodules.  None of these functions carry business logic — they
are thin wrappers around the Basilisk / ic_python_db APIs.
"""

import json

from basilisk import CallResult, Principal, ic
from models import Settings
from services import BasiliskIntrospectionService, FileRegistryService

# ── Application constants ─────────────────────────────────────────────────

VERSION = "0.1.0"
# The anonymous principal — used to detect unauthenticated callers.
ANONYMOUS = "2vxsx-fae"


# ── Candid text parsers ──────────────────────────────────────────────────────
# These avoid the `re` module, which is only partially available in the runtime.

def _principals_in(s: str) -> list:
    """Extract every `principal "<id>"` value from a decoded-candid string."""
    out = []
    marker = 'principal "'
    i = 0
    while True:
        j = s.find(marker, i)
        if j < 0:
            break
        start = j + len(marker)
        end = s.find('"', start)
        if end < 0:
            break
        out.append(s[start:end])
        i = end + 1
    return out


def _parse_principal_subnet_auth_map(decoded: str) -> dict:
    """Map principal -> subnet ids from CMC ``get_principals_authorized…`` output."""
    out = {}
    data_idx = decoded.find("data = vec")
    if data_idx < 0:
        return out
    segment = decoded[data_idx:]
    for part in segment.split("record {")[1:]:
        principals = _principals_in(part)
        if not principals:
            continue
        out[principals[0]] = principals[1:]
    return out


def _candid_number_at(s: str, start: int):
    """Parse a Candid-rendered integer (underscores allowed) at ``start``."""
    end = start
    while end < len(s) and (s[end].isdigit() or s[end] == '_'):
        end += 1
    num = s[start:end].replace('_', '')
    return int(num) if num else None


def _typed_nats_in(s: str, type_marker: str) -> list:
    """Extract every ``<number> : <type_marker>`` value from decoded candid."""
    out = []
    i = 0
    while True:
        j = s.find(type_marker, i)
        if j < 0:
            break
        k = j - 1
        while k >= 0 and s[k] == ' ':
            k -= 1
        end = k + 1
        start = end
        while start - 1 >= 0 and (s[start - 1].isdigit() or s[start - 1] == '_'):
            start -= 1
        num = s[start:end].replace('_', '')
        if num:
            try:
                out.append(int(num))
            except ValueError:
                pass
        i = j + len(type_marker)
    return out


def _nat64s_in(s: str) -> list:
    """Extract every `<number> : nat64` value from a decoded-candid string."""
    return _typed_nats_in(s, ": nat64")


def _nats_in(s: str) -> list:
    """Extract every `<number> : nat` value from a decoded-candid string."""
    return _typed_nats_in(s, ": nat")


_VARIANT_ERR_MARKERS = (
    "Err", "InsufficientFunds", "BadFee", "TxTooOld", "TxDuplicate",
    "TxCreatedInFuture", "GenericError",
)


def _variant_first_number(decoded: str):
    """First numeric payload from a Candid variant (``Ok =``, numeric tag, or nat/nat64)."""
    if not decoded:
        return None
    for err in _VARIANT_ERR_MARKERS:
        if err in decoded:
            return None
    marker = "Ok = "
    j = decoded.find(marker)
    if j >= 0:
        return _candid_number_at(decoded, j + len(marker))
    eq = decoded.find(" = ")
    if eq >= 0 and "variant" in decoded:
        val = _candid_number_at(decoded, eq + 3)
        if val is not None:
            return val
    vals = _nat64s_in(decoded)
    if vals:
        return vals[0]
    vals = _nats_in(decoded)
    return vals[0] if vals else None


# ── Response helpers ─────────────────────────────────────────────────────────

def _ok(**kw) -> str:
    kw.setdefault("ok", True)
    return json.dumps(kw)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


# ── IC caller identity ────────────────────────────────────────────────────────

def _caller() -> str:
    return ic.caller().to_str()


def _is_controller() -> bool:
    return ic.is_controller(ic.caller())


# ── Inter-canister call result unwrapping ─────────────────────────────────────

def unwrap_call_result(cr: CallResult):
    """Return the Ok payload of a CallResult or raise on Err."""
    ok = getattr(cr, "Ok", None)
    err = getattr(cr, "Err", None)
    if ok is None and err is not None:
        raise Exception(f"inter-canister call failed: {err}")
    return ok if ok is not None else cr


# ── Settings singleton ────────────────────────────────────────────────────────

def _settings() -> Settings:
    list(Settings.instances())
    s = Settings["singleton"]
    if s is None:
        s = Settings(key="singleton")
        s.version = VERSION
    return s


# ── Inter-canister service factories ─────────────────────────────────────────

def _file_registry() -> FileRegistryService:
    s = _settings()
    fr = (s.file_registry_canister_id or "").strip()
    if not fr:
        raise Exception("file_registry_canister_id is not configured (see set_settings)")
    return FileRegistryService(Principal.from_str(fr))


def _canister_call(canister_id: str, method: str, arg: str):
    """Generator: relay a single text-in/text-out call to a canister's
    introspection endpoint and return the decoded text reply."""
    svc = BasiliskIntrospectionService(Principal.from_str(canister_id))
    res = yield getattr(svc, method)(arg)
    return unwrap_call_result(res)
