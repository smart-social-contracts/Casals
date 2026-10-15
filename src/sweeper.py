"""The wasm installed on a canister just before deletion. The IC cannot pull
cycles out of a canister, so this one-method module sends them: `sweep`
deposits all but RESERVE cycles to its caller, who must be a controller."""

RESERVE = 100_000_000_000  # pays for the deposit call and its reply, even on large subnets
NAME, REPLY, DENIED, FAILED, ARG, BALANCE = 0, 16, 32, 48, 64, 128
I32, I64 = 0x7F, 0x7E
IMPORTS = [  # ic0 (name, params, results)
    ("msg_caller_size", [], [I32]), ("msg_caller_copy", [I32] * 3, []), ("is_controller", [I32] * 2, [I32]),
    ("canister_cycle_balance128", [I32], []), ("call_new", [I32] * 8, []), ("call_data_append", [I32] * 2, []),
    ("call_cycles_add128", [I64] * 2, []), ("call_perform", [], [I32]), ("msg_reply_data_append", [I32] * 2, []),
    ("msg_reply", [], []), ("trap", [I32] * 2, []),
]
SWEEP, ON_REPLY, ON_REJECT = range(len(IMPORTS), len(IMPORTS) + 3)
N, AMOUNT = b"\x20\x00", b"\x20\x01"  # locals: caller size (i32), amount (i64)
END = b"\x0b"


def leb(n, signed=False):
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        last = (n == 0 and not byte & 0x40) or (n == -1 and byte & 0x40) if signed else n == 0
        out.append(byte if last else byte | 0x80)
        if last:
            return bytes(out)


def vec(items):
    return leb(len(items)) + b"".join(items)


def blob(data):
    return leb(len(data)) + data


def section(sid, items):
    return bytes([sid]) + blob(vec(items))


def i32(*values):
    return b"".join(b"\x41" + leb(v, True) for v in values)


def call(name):
    return b"\x10" + leb(next(i for i, imp in enumerate(IMPORTS) if imp[0] == name))


def trap_if(cond, msg, size):
    return cond + b"\x04\x40" + i32(msg, size) + call("trap") + END


def deposit_arg():
    """Candid `(record { canister_id : principal })` up to the principal's bytes."""
    h = 0
    for b in b"canister_id":
        h = (h * 223 + b) % 2**32
    return b"DIDL\x01\x6c\x01" + leb(h) + b"\x68\x01\x00\x01"


def sweep():
    caller = ARG + len(deposit_arg()) + 1
    return b"".join([
        b"\x02\x01\x7f\x01\x7e", call("msg_caller_size"), b"\x21\x00",
        i32(caller, 0), N, call("msg_caller_copy"), i32(caller - 1), N, b"\x3a\x00\x00",
        trap_if(i32(caller) + N + call("is_controller") + b"\x45", DENIED, 16),
        i32(BALANCE), call("canister_cycle_balance128"),
        i32(BALANCE), b"\x29\x03\x00\x42", leb(RESERVE, True), b"\x7d\x21\x01",
        i32(0, 0, NAME, 14, 0, 0, 1, 0), call("call_new"),
        i32(ARG, caller - ARG), N, b"\x6a", call("call_data_append"),
        b"\x42\x00", AMOUNT, call("call_cycles_add128"),
        trap_if(call("call_perform"), FAILED, 14), END,
    ])


def module():
    types = [b"\x60" + vec([bytes([p]) for p in ps]) + vec([bytes([r]) for r in rs]) for _, ps, rs in IMPORTS]
    on_reply = b"\x00" + i32(REPLY, 6) + call("msg_reply_data_append") + call("msg_reply") + END
    on_reject = b"\x00" + i32(FAILED, 14) + call("trap") + END
    data = [(NAME, b"deposit_cycles"), (REPLY, b"DIDL\x00\x00"), (DENIED, b"controllers only"),
            (FAILED, b"deposit failed"), (ARG, deposit_arg())]
    return b"\x00asm\x01\x00\x00\x00" + b"".join([
        section(1, types + [b"\x60\x00\x00", b"\x60\x01\x7f\x00"]),
        section(2, [blob(b"ic0") + blob(name.encode()) + b"\x00" + leb(i) for i, (name, _, _) in enumerate(IMPORTS)]),
        section(3, [leb(len(IMPORTS)), leb(len(IMPORTS) + 1), leb(len(IMPORTS) + 1)]),
        section(4, [b"\x70\x00\x02"]),
        section(5, [b"\x00\x01"]),
        section(7, [blob(b"canister_update sweep") + b"\x00" + leb(SWEEP)]),
        section(9, [b"\x00" + i32(0) + END + vec([leb(ON_REPLY), leb(ON_REJECT)])]),
        section(10, [blob(sweep()), blob(on_reply), blob(on_reject)]),
        section(11, [b"\x00" + i32(at) + END + blob(payload) for at, payload in data]),
    ])
