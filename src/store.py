from basilisk import Opt, Principal, Record, Service, Variant, Vec, blob, nat, service_query, service_update, text, void

import chain


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


class Assets(Service):
    @service_update
    def grant_permission(self, arg: GrantArg) -> void: ...

    @service_update
    def revoke_permission(self, arg: RevokeArg) -> void: ...

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


def files(store_id, prefix=""):
    entries = chain.ok((yield Assets(Principal.from_str(store_id)).list({"start": None, "length": None})))
    return {e["key"]: bytes(enc["sha256"]).hex() for e in entries if e["key"].startswith(prefix)
            for enc in e["encodings"] if enc["content_encoding"] == "identity" and enc["sha256"]}


def permit(store_id, principal, allow):
    svc = Assets(Principal.from_str(store_id))
    if allow:
        chain.ok((yield svc.grant_permission({"to_principal": Principal.from_str(principal), "permission": {"Commit": None}})))
    else:
        chain.ok((yield svc.revoke_permission({"of_principal": Principal.from_str(principal), "permission": {"Commit": None}})))
