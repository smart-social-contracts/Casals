"""Inter-canister Basilisk Service definitions.

These thin wrappers tell Basilisk how to call three external canisters:

  FileRegistryService        — reads WASM bytes from the file-registry
  AssetCanisterService       — grants permission + uploads assets to a
                               certified-assets frontend canister
  BasiliskIntrospectionService — relays __browse__ / __shell__ calls to
                               managed canisters for the dashboard
"""

from basilisk import (
    Opt,
    Principal,
    Record,
    Service,
    Variant,
    blob,
    service_query,
    service_update,
    text,
    void,
)


# ── File-registry ─────────────────────────────────────────────────────────

class FileRegistryService(Service):
    """Pulls authorized WASM bytes from the file-registry canister."""

    @service_query
    def get_file_size_icc(self, namespace: text, path: text) -> text: ...

    @service_query
    def get_file_chunk_icc(self, namespace: text, path: text, offset: text, length: text) -> text: ...

    @service_query
    def list_files_icc(self, namespace: text) -> text: ...


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


class AssetCanisterService(Service):
    @service_update
    def grant_permission(self, arg: GrantPermissionArg) -> void: ...

    @service_update
    def store(self, arg: StoreArg) -> void: ...


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
