from basilisk import Duration, Principal, ic
from basilisk.canisters.management import management_canister as mgmt

ANONYMOUS = "2vxsx-fae"
MGMT = "aaaaa-aa"
CMC = "rkp4c-7iaaa-aaaaa-aaaca-cai"
LEDGER = "ryjl3-tyaaa-aaaaa-aaaba-cai"
CYCLES_LEDGER = "um5iw-rqaaa-aaaaq-qaaba-cai"
ICP_FEE = 10_000
ESCAPES = {"n": b"\n", "r": b"\r", "t": b"\t", '"': b'"', "\\": b"\\", "'": b"'"}


def caller():
    return ic.caller().to_str()


def is_controller(principal):
    return principal != ANONYMOUS and ic.is_controller(Principal.from_str(principal))


def self_id():
    return ic.id().to_str()


def now():
    return ic.time() // 1_000_000_000


def balance():
    return int(ic.canister_balance128())


def set_timer(secs, cb):
    return ic.set_timer(Duration(secs), cb)


def ok(res):
    if getattr(res, "Err", None) is not None:
        raise Exception(f"call failed: {res.Err}")
    return getattr(res, "Ok", res)


def target(cid):
    return {"canister_id": Principal.from_str(cid)}


def blob(data):
    return '"' + "".join(f"\\{b:02x}" for b in data) + '"'


def principals(items):
    return "vec { " + "; ".join(f'principal "{p}"' for p in items) + " }"


def call(cid, method, candid="()", cycles=0):
    res = yield ic.call_raw(Principal.from_str(cid), method, ic.candid_encode(candid), cycles)
    return ic.candid_decode(ok(res))


def call_text(cid, method, arg=None):
    # arg None: a method that takes no argument.
    candid = "()" if arg is None else '("' + arg.replace("\\", "\\\\").replace('"', '\\"') + '")'
    reply = yield from call(cid, method, candid)
    return unquote(reply)


def unquote(decoded):
    body, out, i = decoded[decoded.index('"') + 1:decoded.rindex('"')], bytearray(), 0
    while i < len(body):
        if body[i] != "\\":
            out += body[i].encode()
            i += 1
        elif body[i + 1] in ESCAPES:
            out += ESCAPES[body[i + 1]]
            i += 2
        elif body[i + 1] == "u":
            end = body.index("}", i)
            out += chr(int(body[i + 3:end], 16)).encode()
            i = end + 1
        else:
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
    return out.decode()


def first_principal(text):
    start = text.find('principal "') + 11
    if start < 11:
        raise Exception(text[:300])
    return text[start:text.index('"', start)]


def create(controllers, cycles, subnet=""):
    if not subnet:
        settings = {"controllers": [Principal.from_str(c) for c in controllers]}
        return ok((yield mgmt.create_canister({"settings": settings}).with_cycles(cycles)))["canister_id"].to_str()
    kind = subnet[5:] if subnet.startswith("type:") else ""
    if subnet.startswith("type:") and not kind.isalnum():
        raise ValueError(f"bad subnet type '{kind}'")
    # Candid text: the subnet comes from callers, so only a parsed principal or an alphanumeric type goes in.
    selection = (f'Filter = record {{ subnet_type = opt "{kind}" }}' if kind
                 else f'Subnet = record {{ subnet = principal "{Principal.from_str(subnet).to_str()}" }}')
    reply = yield from call(CMC, "create_canister", f"(record {{ subnet_selection = opt variant {{ {selection} }}; "
                            f"settings = opt record {{ controllers = opt {principals(controllers)} }} }})", cycles)
    return first_principal(reply)


def status(cid):
    s = ok((yield mgmt.canister_status(target(cid))))
    return {"status": next(iter(s["status"])), "cycles": int(s["cycles"]),
            "module_hash": bytes(s["module_hash"]).hex() if s["module_hash"] else "",
            "controllers": [c.to_str() for c in s["settings"]["controllers"]]}


def update_settings(cid, controllers=None, freezing=None, viewers=None, public_logs=False):
    parts = [f"controllers = opt {principals(controllers)}" if controllers is not None else "",
             f"freezing_threshold = opt ({freezing} : nat)" if freezing is not None else "",
             f"status_visibility = opt variant {{ allowed_viewers = {principals(viewers)} }}" if viewers else "",
             "log_visibility = opt variant { public }" if public_logs else ""]
    settings = "; ".join(p for p in parts if p)
    yield from call(MGMT, "update_settings",
                    f'(record {{ canister_id = principal "{cid}"; settings = record {{ {settings} }} }})')


def lifecycle(cid, method):
    yield from call(MGMT, method, f'(record {{ canister_id = principal "{cid}" }})')


def install(cid, wasm, wasm_hash, arg, mode):
    yield from lifecycle(cid, "clear_chunk_store")
    hashes = []
    for i in range(0, len(wasm), 1 << 20):
        up = ok((yield mgmt.upload_chunk({"canister_id": Principal.from_str(cid), "chunk": wasm[i:i + (1 << 20)]})))
        hashes.append(f"record {{ hash = blob {blob(bytes(up['hash']))} }}")
    mode = ("variant { upgrade = opt record { wasm_memory_persistence = opt variant { keep } } }"
            if mode == "keep" else f"variant {{ {mode} }}")
    yield from call(MGMT, "install_chunked_code", f'(record {{ mode = {mode}; target_canister = principal "{cid}"; '
                    f'store_canister = opt principal "{cid}"; chunk_hashes_list = vec {{ {"; ".join(hashes)} }}; '
                    f"wasm_module_hash = blob {blob(bytes.fromhex(wasm_hash))}; "
                    f"arg = blob {blob(ic.candid_encode(arg))} }})")
    yield from lifecycle(cid, "clear_chunk_store")


def install_code(cid, wasm):
    ok((yield mgmt.install_code({"mode": {"reinstall": None}, "canister_id": Principal.from_str(cid),
                                 "wasm_module": wasm, "arg": b""})))


def snapshot(cid, action, snap=""):
    if action == "take":
        return bytes(ok((yield mgmt.take_canister_snapshot(target(cid))))["id"]).hex()
    method = mgmt.load_canister_snapshot if action == "load" else mgmt.delete_canister_snapshot
    ok((yield method({**target(cid), "snapshot_id": bytes.fromhex(snap)})))


def deposit(cid, cycles):
    yield from call(MGMT, "deposit_cycles", f'(record {{ canister_id = principal "{cid}" }})', cycles)


def deposit_account(owner, cycles):
    yield from call(CYCLES_LEDGER, "deposit",
                    f'(record {{ to = record {{ owner = principal "{owner}"; subaccount = null }}; memo = null }})', cycles)


def label(name):
    """How an untyped reply names the field `name`: by its candid hash, e.g. `5_035_232`."""
    h = 0
    for ch in name.encode():
        h = (h * 223 + ch) % 2**32
    return f"{h:_}"


def number(reply, marker):
    marker = marker if marker in reply or not marker.isidentifier() else label(marker)
    if marker not in reply:
        raise Exception(reply[:300])
    return int("".join(ch for ch in reply.split(marker, 1)[1].split(":")[0] if ch.isdigit()))


def all_principals(text):
    return [part.split('"', 1)[0] for part in text.split('principal "')[1:]]


def account(owner, sub=bytes(32)):
    return bytes.fromhex(Principal.from_str(owner).to_account_id(sub).to_str()[2:])


def convert_icp():
    me = Principal.from_str(self_id()).bytes
    reply = yield from call(LEDGER, "account_balance", f"(record {{ account = blob {blob(account(self_id()))} }})")
    e8s = number(reply, "e8s") - ICP_FEE
    if e8s <= ICP_FEE:
        return 0
    to = account(CMC, bytes([len(me)]) + me + bytes(31 - len(me)))
    reply = yield from call(LEDGER, "transfer", f"(record {{ memo = 1347768404 : nat64; amount = record {{ e8s = {e8s} "
                            f": nat64 }}; fee = record {{ e8s = {ICP_FEE} : nat64 }}; from_subaccount = null; "
                            f"to = blob {blob(to)}; created_at_time = null }})")
    reply = yield from call(CMC, "notify_top_up", f'(record {{ block_index = {number(reply, "Ok")} : nat64; '
                            f'canister_id = principal "{self_id()}" }})')
    return number(reply, "Ok")


def subnets():
    found = all_principals((yield from call(CMC, "get_default_subnets")))
    for entry in (yield from call(CMC, "get_principals_authorized_to_create_canisters_to_subnets")).split("record {")[1:]:
        named = all_principals(entry)
        found += named[1:] if named and named[0] == self_id() else []
    return sorted(set(found))
