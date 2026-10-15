import json

from basilisk import Opt, Principal, Record, Service, Variant, Vec, blob, nat, service_query, service_update, text, void

import chain

CHUNK = 1_900_000


class Permission(Variant, total=False):
    Commit: void


class GrantArg(Record):
    to_principal: Principal
    permission: Permission


class RevokeArg(Record):
    of_principal: Principal
    permission: Permission


class ListArgs(Record):
    start: Opt[nat]
    length: Opt[nat]


class Encoding(Record):
    content_encoding: text
    sha256: Opt[blob]
    length: nat
    modified: int


class Entry(Record):
    key: text
    content_type: text
    encodings: Vec[Encoding]


class GetArg(Record):
    key: text
    accept_encodings: Vec[text]


class Asset(Record):
    content: blob
    content_type: text
    content_encoding: text
    sha256: Opt[blob]
    total_length: nat


class ChunkArg(Record):
    key: text
    content_encoding: text
    index: nat
    sha256: Opt[blob]


class Chunk(Record):
    content: blob


class KeyArg(Record):
    key: text


class ChunkIn(Record):
    batch_id: nat
    content: blob


class ChunkOut(Record):
    chunk_id: nat


class Assets(Service):
    @service_update
    def grant_permission(self, arg: GrantArg) -> void: ...

    @service_update
    def revoke_permission(self, arg: RevokeArg) -> void: ...

    @service_update
    def create_chunk(self, arg: ChunkIn) -> ChunkOut: ...

    @service_update
    def delete_asset(self, arg: KeyArg) -> void: ...

    @service_query
    def list(self, arg: ListArgs) -> Vec[Entry]: ...

    @service_query
    def get(self, arg: GetArg) -> Asset: ...

    @service_query
    def get_chunk(self, arg: ChunkArg) -> Chunk: ...


def read(store_id, key):
    svc = Assets(Principal.from_str(store_id))
    got = chain.ok((yield svc.get({"key": key, "accept_encodings": ["identity"]})))
    pieces, size = [bytes(got["content"])], len(got["content"])
    while size < got["total_length"]:
        arg = {"key": key, "content_encoding": "identity", "index": len(pieces), "sha256": got["sha256"]}
        pieces.append(bytes(chain.ok((yield svc.get_chunk(arg)))["content"]))
        size += len(pieces[-1])
    return b"".join(pieces)


def files(canister_id, prefix=""):
    entries = chain.ok((yield Assets(Principal.from_str(canister_id)).list({"start": None, "length": None})))
    return {e["key"]: {"sha256": bytes(enc["sha256"]).hex(), "type": e["content_type"]}
            for e in entries if e["key"].startswith(prefix)
            for enc in e["encodings"] if enc["content_encoding"] == "identity" and enc["sha256"]}


def lit(s):
    return json.dumps(s, ensure_ascii=False)


def some(value, show=lit):
    return "null" if value is None else f"opt {show(value)}"


def new_asset(f):
    p = f["props"]
    headers = "vec { " + "; ".join(f"record {{ {lit(k)}; {lit(v)} }}" for k, v in sorted((p.get("headers") or {}).items())) + " }"
    return (f'(record {{ key = {lit(f["key"])}; content_type = {lit(f["type"])}; '
            f'max_age = {some(p.get("max_age"), lambda n: f"({n} : nat64)")}; headers = {some(p.get("headers") or None, lambda _: headers)}; '
            f'enable_aliasing = {some(p.get("enable_aliasing"))}; allow_raw_access = {some(p.get("allow_raw_access"))} }})')


def asset_content(f, chunk_ids):
    ids = "; ".join(f"({i} : nat)" for i in chunk_ids)
    return (f'(record {{ key = {lit(f["key"])}; content_encoding = "identity"; chunk_ids = vec {{ {ids} }}; '
            f'sha256 = opt blob {chain.blob(bytes.fromhex(f["sha256"]))} }})')


def apply(canister_id, puts, deletes):
    """Each file in `puts` recreated with its properties (only create_asset sets them all), then `deletes` removed."""
    # Candid text: Basilisk's typed encoding lost the optional header list.
    svc = Assets(Principal.from_str(canister_id))
    batch = chain.number((yield from chain.call(canister_id, "create_batch", "(record {})")), "=") if puts else 0
    for f in puts:
        ids = []
        for i in range(0, max(len(f["data"]), 1), CHUNK):
            ids.append(chain.ok((yield svc.create_chunk({"batch_id": batch, "content": f["data"][i:i + CHUNK]})))["chunk_id"])
        if f["replace"]:
            chain.ok((yield svc.delete_asset({"key": f["key"]})))
        yield from chain.call(canister_id, "create_asset", new_asset(f))
        yield from chain.call(canister_id, "set_asset_content", asset_content(f, ids))
    for key in deletes:
        chain.ok((yield svc.delete_asset({"key": key})))


def permit(store_id, principal, allow):
    svc = Assets(Principal.from_str(store_id))
    if allow:
        chain.ok((yield svc.grant_permission({"to_principal": Principal.from_str(principal), "permission": {"Commit": None}})))
    else:
        chain.ok((yield svc.revoke_permission({"of_principal": Principal.from_str(principal), "permission": {"Commit": None}})))
