"""Generic text-in / text-out inter-canister calls (config_call items)."""

import json

from basilisk import Principal, ic

from arrangement_helpers import candid_text_tuple
from helpers import unwrap_call_result


def config_text_arg(args):
    """Render config args as a Candid text argument, or None for no-arg calls."""
    if args is None:
        return None
    if isinstance(args, str):
        return args
    return json.dumps(args)


def call_text_method_gen(canister_id: str, method: str, text_arg):
    """Generator: invoke a text method via raw Candid encoding."""
    arg = "()" if text_arg is None else candid_text_tuple(text_arg)
    res = yield ic.call_raw(
        Principal.from_str(canister_id), method, ic.candid_encode(arg), 0
    )
    return ic.candid_decode(unwrap_call_result(res))


# Alias used by orchestration_bridge (formerly arrangement.py).
_call_text_method = call_text_method_gen
