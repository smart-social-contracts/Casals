"""Generic text-in / text-out inter-canister calls (config_call items)."""

import json

from basilisk import Principal, ic

from helpers import unwrap_call_result


def candid_text_tuple(s: str) -> str:
    """Wrap a string as a Candid text-literal tuple `("...")`, escaping backslashes
    and quotes."""
    escaped = (s or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def config_text_arg(args):
    """Render config args as a Candid text argument, or None for no-arg calls."""
    if args is None:
        return None
    if isinstance(args, str):
        return args
    return json.dumps(args)


def candid_text_value(decoded: str) -> str:
    """Inverse of `candid_text_tuple`: `("...")` → the text, with Candid escapes
    (`\\"`, `\\\\`, `\\n`, `\\r`, `\\t`, `\\XX` byte, `\\u{XXXX}`) undone. Anything
    that is not a single text literal is returned as is."""
    d = (decoded or "").strip()
    if not (d.startswith("(") and d.endswith(")")):
        return decoded
    d = d[1:-1].strip()  # long values are pretty-printed: `(\n  "…",\n)`
    if d.endswith(","):
        d = d[:-1].rstrip()
    if not (d.startswith('"') and d.endswith('"')):
        return decoded
    body = d[1:-1]
    out = bytearray()
    i = 0
    simple = {"n": b"\n", "r": b"\r", "t": b"\t", '"': b'"', "\\": b"\\", "'": b"'"}
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out += ch.encode("utf-8")
            i += 1
            continue
        nxt = body[i + 1]
        if nxt in simple:
            out += simple[nxt]
            i += 2
        elif nxt == "u" and i + 2 < len(body) and body[i + 2] == "{":
            end = body.index("}", i + 3)
            out += chr(int(body[i + 3:end], 16)).encode("utf-8")
            i = end + 1
        else:
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
    return out.decode("utf-8", errors="replace")


def call_text_method_gen(canister_id: str, method: str, text_arg):
    """Generator: invoke a text-in / text-out method; return the reply text."""
    arg = "()" if text_arg is None else candid_text_tuple(text_arg)
    res = yield ic.call_raw(
        Principal.from_str(canister_id), method, ic.candid_encode(arg), 0
    )
    return candid_text_value(ic.candid_decode(unwrap_call_result(res)))


# Alias used by orchestration_bridge / applier.
_call_text_method = call_text_method_gen
