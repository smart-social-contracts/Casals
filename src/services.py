"""Inter-canister Basilisk Service definitions.

These thin wrappers tell Basilisk how to call two external canisters:

  AssetCanisterService       — the certified-assets canister: the casals-wasms
                               WASM store (reads, permissions, housekeeping)
                               and every frontend canister (asset uploads)
  BasiliskIntrospectionService — relays __browse__ / __shell__ calls to
                               managed canisters for the dashboard
"""

from basilisk import (
    Opt,
    Principal,
    Record,
    Service,
    Variant,
    Vec,
    blob,
    nat,
    service_query,
    service_update,
    text,
    void,
)


# ── Certified-assets canister ─────────────────────────────────────────────
#
# A `frontend` canister can run the DFINITY certified-assets canister,
# which installs empty. After install Casals (the canister's controller)
# grants itself Commit permission and uploads the template's asset via
# `store`, so the canister actually serves a page. Records mirror the
# asset canister's Candid.

class AssetPermission(Variant, total=False):
    Commit: void
    Prepare: void
    ManagePermissions: void


class GrantPermissionArg(Record):
    to_principal: Principal
    permission: AssetPermission


class StoreArg(Record):
    key: text
    content_type: text
    content_encoding: text
    content: blob
    sha256: Opt[blob]


class ListArgs(Record):
    start: Opt[nat]
    length: Opt[nat]


class AssetEncoding(Record):
    content_encoding: text
    sha256: Opt[blob]
    length: nat
    modified: int


class AssetEntry(Record):
    key: text
    content_type: text
    encodings: Vec[AssetEncoding]


# Read side, used against the `casals-wasms` store (see wasm_store.py). `get`
# returns the whole content when it fits in one chunk, else chunk 0 plus
# `total_length`; every chunk but the last has the size of chunk 0.

class GetArg(Record):
    key: text
    accept_encodings: Vec[text]


class EncodedAsset(Record):
    content: blob
    content_type: text
    content_encoding: text
    sha256: Opt[blob]
    total_length: nat


class GetChunkArg(Record):
    key: text
    content_encoding: text
    index: nat
    sha256: Opt[blob]


class ChunkContent(Record):
    content: blob


class RevokePermissionArg(Record):
    of_principal: Principal
    permission: AssetPermission


class DeleteAssetArg(Record):
    key: text


class AssetCanisterService(Service):
    @service_update
    def grant_permission(self, arg: GrantPermissionArg) -> void: ...

    @service_update
    def revoke_permission(self, arg: RevokePermissionArg) -> void: ...

    @service_update
    def store(self, arg: StoreArg) -> void: ...

    @service_update
    def delete_asset(self, arg: DeleteAssetArg) -> void: ...

    @service_query
    def list(self, arg: ListArgs) -> Vec[AssetEntry]: ...

    @service_query
    def get(self, arg: GetArg) -> EncodedAsset: ...

    @service_query
    def get_chunk(self, arg: GetChunkArg) -> ChunkContent: ...


# ── Basilisk introspection relay ──────────────────────────────────────────
#
# A Basilisk canister built with `__basilisk_features__ = ["shell",
# "browse"]` exposes two extra methods. Casals (the canister's controller)
# relays calls to them so the dashboard can inspect / drive a canister
# without the operator being a direct controller of that canister:
#   __browse__(query)  read-only data introspection — public @query
#   __shell__(code)    runs Python in the canister  — controller-only @update
# The on-chain method names are the dunders themselves; the runtime maps a
# Service method to the wire name by its __name__, so the names must match.

class BasiliskIntrospectionService(Service):
    @service_query
    def __browse__(self, query: text) -> text: ...

    @service_update
    def __shell__(self, code: text) -> text: ...
