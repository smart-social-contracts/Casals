"""Sheet v2 — pure validation, placeholder resolution, and navigation helpers.

Standard library only; safe to import from the Basilisk canister and the CLI.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterator

from access_code import is_code_checksum, normalize_code_checksum


SCHEMA_VERSION = 2

MODES = frozenset({"managed", "adopted"})
KINDS = frozenset({"backend", "frontend"})
UPGRADES = frozenset({"upgrade", "reinstall"})
# `conductor.wasms` is the WASM store: a certified-assets canister holding every
# artifact Casals installs (issue #48). It replaced the Basilisk file-registry
# pair (`file_registry` / `file_registry_frontend`), which a sheet may no longer
# declare — validation names the replacement.
CONDUCTOR_KEYS = ("backend", "frontend", "wasms")
REQUIRED_CONDUCTOR_KEYS = ("backend", "frontend", "wasms")
STORE_CONDUCTOR_KEY = "wasms"
LEGACY_CONDUCTOR_KEYS = ("file_registry", "file_registry_frontend")
CONDUCTOR_NAMES = {
    "backend": "casals-backend",
    "frontend": "casals-frontend",
    "wasms": "casals-wasms",
}
# Canister names of the retired file-registry pair; `ensure_core_layout` pools
# any rows still carrying them so the canisters are recycled, not leaked.
LEGACY_CONDUCTOR_NAMES = ("file-registry", "file-registry-frontend")
# Every canister lives on a stand. The sheet's top-level `conductor` and
# `governance` blocks are homed on one synthetic infra section (`Casals`) with
# two stands: `conductor` (casals-backend/-frontend, casals-wasms) and
# `governance` (multisig).
# `conductor.commanders` are kept on the section.
SYNTHETIC_SECTION_CONDUCTOR = "Casals"
TEMPLATE_STAND: dict = {"__template__": True}  # stand context marker: `$stand.*` stays a token
SYNTHETIC_STAND_CONDUCTOR = "conductor"
SYNTHETIC_SECTION_GOVERNANCE = SYNTHETIC_SECTION_CONDUCTOR
SYNTHETIC_STAND_GOVERNANCE = "governance"
SYNTHETIC_STANDS = frozenset({SYNTHETIC_STAND_CONDUCTOR, SYNTHETIC_STAND_GOVERNANCE})
MULTISIG_NAME = "multisig"
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_ENV_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")
_BARE_PLACEHOLDERS = ("$multisig", "$self", "$deployer", "$this")
_PREFIXED_PLACEHOLDERS = ("$canister:", "$principal:", "$stand.", "$env.")
# `baton.hand_off`: false/absent = no hand-off; true = the baton co-controls
# what it manages (Casals stays a controller); "sole" = the baton is the
# controller — Casals installs, hands over and leaves (the sheet must not list
# $self/$deployer on managed members; a realm may list $this to keep its exit key).
HAND_OFF_SOLE = "sole"


def _is_wasm_ref(value: str) -> bool:
    """`family` or `family@version`; names are [A-Za-z0-9._-] and start alphanumeric."""
    parts = value.split("@")
    if len(parts) > 2:
        return False
    for part in parts:
        if not part or not part[0].isalnum() or not set(part) <= _NAME_CHARS:
            return False
    return True


def find_placeholder_tokens(text: str) -> list[str]:
    """Placeholder tokens in ``text``, in order (no regex: the canister has none)."""
    tokens: list[str] = []
    i = 0
    while True:
        i = text.find("$", i)
        if i < 0:
            return tokens
        rest = text[i:]
        token = None
        if rest.startswith("${"):  # `${canister:x}` delimits a placeholder inside a word (URLs)
            end = rest.find("}")
            inner = "$" + rest[2:end]
            if end > 2 and find_placeholder_tokens(inner) == [inner]:
                token = rest[:end + 1]
        if token is None:
            for bare in _BARE_PLACEHOLDERS:
                if rest.startswith(bare) and not (len(rest) > len(bare) and rest[len(bare)] in _NAME_CHARS):
                    token = bare
                    break
        if token is None:
            for prefix in _PREFIXED_PLACEHOLDERS:
                if rest.startswith(prefix):
                    chars = _ENV_CHARS if prefix == "$env." else _NAME_CHARS
                    j = len(prefix)
                    while j < len(rest) and rest[j] in chars:
                        j += 1
                    if j > len(prefix):
                        token = rest[:j]
                    break
        if token is None:
            i += 1
            continue
        tokens.append(token)
        i += len(token)


def _as_text(value: Any) -> str:
    """A resolved value spliced into a string: text as is, anything else as
    JSON (`true`, `3`, `null`) so it reads the same in JS, JSON and Candid."""
    return value if isinstance(value, str) else json.dumps(value)


def _unbrace(token: str) -> str:
    return "$" + token[2:-1] if token.startswith("${") else token


class UnresolvedPlaceholder(Exception):
    """A placeholder could not be resolved."""

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        super().__init__(f"unresolved placeholder {name!r} at {path}")


class ResolveContext:
    """Runtime bindings used when resolving placeholders."""

    def __init__(self, deployer=None, self_id=None, canister_ids=None, env_values=None):
        self.deployer = deployer
        self.self_id = self_id
        self.canister_ids = dict(canister_ids or {})
        self.env_values = dict(env_values or {})


def find_placeholders(value: Any) -> set[str]:
    """Return every placeholder token found in nested dict/list/str values."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_unbrace(t) for t in find_placeholder_tokens(value))
    elif isinstance(value, dict):
        for v in value.values():
            found.update(find_placeholders(v))
    elif isinstance(value, list):
        for item in value:
            found.update(find_placeholders(item))
    return found


WASM_NAMESPACE = "wasm"  # store namespace for every wasm; shared by CLI and canister


def registry_path(family: str, version: str | None) -> str:
    """Path of a wasm inside WASM_NAMESPACE: one rule, shared by CLI and canister."""
    return f"{family}@{version or 'main'}.wasm.gz"


def store_key(namespace: str, path: str) -> str:
    """Asset key of a (namespace, path) pair in the `casals-wasms` store. The
    registry's two-level address maps onto one key, so catalog rows keep their
    `registry_namespace` / `registry_path` whichever store serves them."""
    ns = (namespace or "").strip().strip("/")
    p = (path or "").strip().lstrip("/")
    return f"/{ns}/{p}" if ns else f"/{p}"


BUNDLE_MANIFEST = ".casals-bundle.json"  # metadata inside a bundle; never hashed, never served


def _is_hex64(value) -> bool:
    """A 64-hex digest. Written without ``re`` on purpose: the canister's
    ``re`` (RustPython) lacks ``fullmatch``."""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def bundle_hash(hashes: dict) -> str:
    """The bundle hash of docs/BUNDLES.md: sha256 of the sorted ``sha256sum``
    listing (``"<sha256>  <path>\n"`` per file). One rule, shared by the CLI,
    the conductor and (re-implemented) the browser."""
    text = "".join(f"{hashes[p]}  {p}\n" for p in sorted(hashes) if p != BUNDLE_MANIFEST)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def publish_pins(sheet: dict) -> dict:
    """``{namespace: bundle sha256}`` for every pinned `registry.publish` row."""
    out = {}
    for entry in (sheet.get("registry") or {}).get("publish") or []:
        if isinstance(entry, dict) and entry.get("path") and entry.get("sha256"):
            out[str(entry["path"])] = str(entry["sha256"]).strip().lower()
    return out


SYNC_AUTO = "auto"
SYNC_MANUAL = "manual"
SYNC_MODES = (SYNC_AUTO, SYNC_MANUAL)


def sync_mode(section: dict | None, stand: dict | None = None) -> str:
    """`sync` of a stand (inherits its section's) or a section. ``manual``
    sections/stands are observed by the planner — drift is reported — but
    never acted upon unless a run targets them explicitly (#51)."""
    for scope in (stand, section):
        if isinstance(scope, dict):
            mode = str(scope.get("sync") or "").strip().lower()
            if mode:
                return mode
    return SYNC_AUTO


def scope_modes(sheet: dict) -> dict:
    """``{"sections": {name: mode}, "stands": {name: (section, mode)}}`` — the
    effective sync mode of every declared section and stand."""
    out = {"sections": {}, "stands": {}}
    for section in sheet.get("sections") or []:
        if not isinstance(section, dict):
            continue
        sname = (section.get("name") or "").strip()
        out["sections"][sname] = sync_mode(section)
        for stand in section.get("stands") or []:
            if isinstance(stand, dict) and (stand.get("name") or "").strip():
                out["stands"][stand["name"].strip()] = (sname, sync_mode(section, stand))
    return out


def store_namespace_prefix(namespace: str) -> str:
    """Key prefix under which every file of ``namespace`` lives in the store."""
    ns = (namespace or "").strip().strip("/")
    return f"/{ns}/" if ns else "/"


def wasm_ref(s: str) -> tuple[str, str | None]:
    """Split a wasm reference into ``(family, version|None)``."""
    text = (s or "").strip()
    if "@" in text:
        family, version = text.split("@", 1)
        return family, version
    return text, None


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, ASCII-only."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sheet_hash(sheet: dict) -> str:
    """SHA-256 hex digest of the sheet's canonical JSON."""
    return hashlib.sha256(canonical_json(sheet).encode("utf-8")).hexdigest()


def environments(sheet: dict) -> list[str]:
    """Return environment names declared in the sheet."""
    envs = sheet.get("environments")
    if not isinstance(envs, dict):
        return []
    return sorted(envs.keys())


def env_block(sheet: dict, env: str) -> dict:
    """Return ``environments.<env>`` or an empty dict."""
    envs = sheet.get("environments")
    if not isinstance(envs, dict):
        return {}
    block = envs.get(env)
    return block if isinstance(block, dict) else {}


def canister_names(sheet: dict) -> list[str]:
    """Return every canister name in sheet order."""
    return [name for _, _, name, _ in _iter_named_canisters(sheet)]


def find_canister(sheet: dict, name: str) -> tuple[dict, dict, dict, str] | None:
    """Return ``(section, stand, canister, logical_name)`` or ``None``."""
    for section, stand, cname, canister in _iter_named_canisters(sheet):
        if cname == name:
            return section, stand, canister, cname
    return None


def stand_of(sheet: dict, name: str) -> dict | None:
    """Return the stand dict containing ``name``, or ``None``."""
    found = find_canister(sheet, name)
    if not found:
        return None
    return found[1]


def baton_hand_off_mode(baton) -> str:
    """`""` (no hand-off), `"co"` (`hand_off: true`) or `"sole"`."""
    if not isinstance(baton, dict):
        return ""
    v = baton.get("hand_off")
    if v == HAND_OFF_SOLE:
        return HAND_OFF_SOLE
    return "co" if v is True else ""


def baton_manages(stand: dict) -> list[dict]:
    """Stand members named by `baton.manages`: a list of roles, or `"*"` for
    every member but the baton itself (numbered, runtime-minted ones included)."""
    baton = stand.get("baton")
    if not isinstance(baton, dict):
        return []
    manages = baton.get("manages")
    if manages == "*":
        return [c for c in stand.get("canisters") or []
                if isinstance(c, dict) and not (c.get("name") or "").endswith("-baton")]
    if not isinstance(manages, list):
        return []
    out: list[dict] = []
    for role in manages:
        for m in stand_members(stand, role):
            if m not in out:
                out.append(m)
    return out


def baton_managed_members(stand: dict) -> list[dict]:
    """Stand members the baton controls (co- or sole): `baton.manages` when `hand_off` is set."""
    if not baton_hand_off_mode(stand.get("baton")) or stand_member(stand, "baton") is None:
        return []
    return baton_manages(stand)


def baton_commanders(baton: dict) -> list[dict]:
    """`baton.commanders` normalized to `[{principal, weight}]`, sorted by principal.
    A bare principal weighs 1; `{"principal": "$multisig", "weight": 2}` can pass
    the threshold alone."""
    out: dict[str, int] = {}
    for entry in (baton or {}).get("commanders") or []:
        if isinstance(entry, dict):
            p = str(entry.get("principal") or "").strip()
            try:
                w = int(entry.get("weight", 1))
            except (TypeError, ValueError):
                w = 1
        else:
            p, w = str(entry or "").strip(), 1
        if p:
            out[p] = max(out.get(p, 0), w)
    return [{"principal": p, "weight": out[p]} for p in sorted(out)]


def stand_member(stand: dict, role: str) -> dict | None:
    """The canister in ``stand`` playing ``role``: name suffix `-<role>`
    (`-baton`, `-token`, …); for `backend`/`frontend` the `kind` also counts,
    batons excluded."""
    role = (role or "").strip().lower()
    if not role:
        return None
    canisters = [c for c in (stand.get("canisters") or []) if isinstance(c, dict)]
    for canister in canisters:
        if canister.get("name", "").endswith("-" + role):
            return canister
    if role in ("backend", "frontend"):
        for canister in canisters:
            if canister.get("kind") == role and not canister.get("name", "").endswith("-baton"):
                return canister
    return None


def stand_members(stand: dict, role: str) -> list[dict]:
    """Every canister in ``stand`` playing ``role``, numbered ones included
    (`-quarter` matches `alpha-quarter-1`, `alpha-quarter-2`)."""
    role = (role or "").strip().lower()
    if not role:
        return []
    one = stand_member(stand, role)
    out = [one] if one else []
    for c in stand.get("canisters") or []:
        if not isinstance(c, dict) or c is one:
            continue
        head, _, tail = c.get("name", "").rpartition("-")
        if tail.isdigit() and head.endswith("-" + role):
            out.append(c)
    return out


def iter_canisters(sheet: dict) -> Iterator[tuple[dict, dict, str, dict]]:
    """Yield ``(section, stand, name, canister)`` for every declared canister.

    Conductor entries appear under synthetic section ``Casals`` / stand
    ``conductor``; governance ``multisig`` under ``System`` / ``governance``.
    """
    return _iter_named_canisters(sheet)


def resolve(sheet: dict, env: str, ctx: ResolveContext) -> dict:
    """Return a deep copy of ``sheet`` with all placeholders resolved."""
    result, _unresolved = resolve_partial(sheet, env, ctx, partial=False)
    return result


def resolve_partial(
    sheet: dict,
    env: str,
    ctx: ResolveContext,
    *,
    partial: bool = True,
) -> tuple[dict, set[str]]:
    """Resolve placeholders; when ``partial`` is True, leave unknowns in place."""
    unresolved: set[str] = set()
    env_data = _env_lookup_root(sheet, env, ctx)

    def resolve_value(value: Any, path: str, stand: dict | None, this: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: resolve_value(v, f"{path}.{k}", stand, this) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_value(v, f"{path}[{i}]", stand, this) for i, v in enumerate(value)]
        if not isinstance(value, str):
            return value
        tokens = find_placeholder_tokens(value)
        if not tokens:
            return value
        if len(tokens) == 1 and value == tokens[0]:
            try:
                return _resolve_token(tokens[0], path, stand, env_data, ctx, this)
            except UnresolvedPlaceholder as exc:
                if partial:
                    unresolved.add(exc.name)
                    return value
                raise
        out = value
        for token in sorted(set(tokens), key=len, reverse=True):  # `$env.a.bc` before its prefix `$env.a.b`
            try:
                replacement = _resolve_token(token, path, stand, env_data, ctx, this)
            except UnresolvedPlaceholder as exc:
                if partial:
                    unresolved.add(exc.name)
                    continue
                raise
            if not isinstance(replacement, (str, int, float, bool)) and replacement is not None:
                raise UnresolvedPlaceholder(token, path)
            out = out.replace(token, _as_text(replacement))
        return out

    copied = json.loads(json.dumps(sheet))
    for section, stand, name, canister in _iter_named_canisters(copied):
        _resolve_canister_tree(canister, "canister", stand, resolve_value, name)
    if isinstance(copied.get("conductor"), dict):
        for key in CONDUCTOR_KEYS:
            block = copied["conductor"].get(key)
            if isinstance(block, dict):
                _resolve_canister_tree(block, f"conductor.{key}", None, resolve_value, CONDUCTOR_NAMES[key])
        if "commanders" in copied["conductor"]:
            copied["conductor"]["commanders"] = resolve_value(
                copied["conductor"]["commanders"], "conductor.commanders", None
            )
        if "settings" in copied["conductor"]:
            copied["conductor"]["settings"] = resolve_value(
                copied["conductor"]["settings"], "conductor.settings", None
            )
    gov = copied.get("governance")
    if isinstance(gov, dict) and isinstance(gov.get("multisig"), dict):
        _resolve_canister_tree(gov["multisig"], "governance.multisig", None, resolve_value, MULTISIG_NAME)
    if isinstance(copied.get("domains"), list):
        copied["domains"] = resolve_value(copied["domains"], "domains", None)
    if isinstance(copied.get("cycles"), dict):
        copied["cycles"] = resolve_value(copied["cycles"], "cycles", None)
    for i, section in enumerate(copied.get("sections") or []):
        if not isinstance(section, dict):
            continue
        spath = f"sections[{i}]"
        if "commanders" in section:
            section["commanders"] = resolve_value(section["commanders"], f"{spath}.commanders", None)
        if isinstance(section.get("stand_template"), dict):
            tmpl = section["stand_template"]
            for j, canister in enumerate(tmpl.get("canisters") or []):
                if isinstance(canister, dict):
                    # `$this` stays a token too: the member is rendered per stand later
                    _resolve_canister_tree(
                        canister, f"{spath}.stand_template.canisters[{j}]", TEMPLATE_STAND, resolve_value, None
                    )
            if "controllers" in tmpl:
                tmpl["controllers"] = resolve_value(
                    tmpl["controllers"], f"{spath}.stand_template.controllers", TEMPLATE_STAND
                )
            if "commanders" in tmpl:
                tmpl["commanders"] = resolve_value(
                    tmpl["commanders"], f"{spath}.stand_template.commanders", TEMPLATE_STAND
                )
            if "created_by" in tmpl:
                tmpl["created_by"] = resolve_value(
                    tmpl["created_by"], f"{spath}.stand_template.created_by", None
                )
        for j, stand in enumerate(section.get("stands") or []):
            if not isinstance(stand, dict):
                continue
            stpath = f"{spath}.stands[{j}]"
            if "commanders" in stand:
                stand["commanders"] = resolve_value(stand["commanders"], f"{stpath}.commanders", stand)
            if isinstance(stand.get("baton"), dict):
                stand["baton"] = resolve_value(stand["baton"], f"{stpath}.baton", stand)
            for k, canister in enumerate(stand.get("canisters") or []):
                if isinstance(canister, dict):
                    _resolve_canister_tree(
                        canister, f"{stpath}.canisters[{k}]", stand, resolve_value, canister.get("name")
                    )
    _materialize_ic_domains(copied)
    return copied, unresolved


IC_DOMAINS_FILE = "/.well-known/ic-domains"


def _materialize_ic_domains(sheet: dict) -> None:
    """A frontend named in `domains` serves `/.well-known/ic-domains` listing its
    hosts (the gateway reads it to register a custom domain). It is derived here
    so the hosts live in the sheet, never in a dist. Wildcards and empty hosts
    (an environment without a domain) are skipped."""
    hosts: dict[str, list[str]] = {}
    for dom in sheet.get("domains") or []:
        host = (dom.get("host") or "").strip() if isinstance(dom, dict) else ""
        if host and "*" not in host and dom.get("canister"):
            hosts.setdefault(dom["canister"], []).append(host)
    for name, names in hosts.items():
        found = find_canister(sheet, name)
        if found and found[2].get("kind") == "frontend":
            found[2].setdefault("files", {})[IC_DOMAINS_FILE] = "\n".join(names) + "\n"


def validate(sheet: dict, env: str | None = None) -> list[str]:
    """Validate structural sheet v2 rules; return human-readable error messages."""
    errors: list[str] = []

    if not isinstance(sheet, dict):
        return ["sheet must be a JSON object"]
    if sheet.get("version") != SCHEMA_VERSION:
        errors.append(f"version must be {SCHEMA_VERSION}")

    names: dict[str, str] = {}
    env_names = environments(sheet)
    env_targets = [env] if env else env_names

    _validate_environments(sheet, env_targets, errors)
    _validate_registry(sheet, env, errors)
    _validate_cycles_block(sheet.get("cycles"), "cycles", errors)
    conductor = sheet.get("conductor")
    if not isinstance(conductor, dict):
        errors.append("conductor must be an object")
    else:
        for key in CONDUCTOR_KEYS:
            path = f"conductor.{key}"
            block = conductor.get(key)
            if block is None and key not in REQUIRED_CONDUCTOR_KEYS:
                continue
            if not isinstance(block, dict):
                errors.append(
                    f"{path} must be an object"
                    + (" (the casals-wasms WASM store; see issue #48)" if key == STORE_CONDUCTOR_KEY else "")
                )
                continue
            cname = CONDUCTOR_NAMES[key]
            _register_name(names, cname, path, errors)
            _validate_canister(block, path, errors, in_sections=False)
            _check_raw_principals(block, path, errors)
        if isinstance(conductor.get("wasms"), dict) and (conductor["wasms"].get("kind") or "frontend") != "frontend":
            errors.append("conductor.wasms.kind must be frontend (a certified-assets canister)")
        for key in LEGACY_CONDUCTOR_KEYS:
            if key in conductor:
                errors.append(
                    f"conductor.{key} is no longer supported: the file-registry was replaced by the "
                    "casals-wasms store (conductor.wasms); remove the block and its registry.wasms row"
                )

    governance = sheet.get("governance")
    if governance is not None:
        if not isinstance(governance, dict):
            errors.append("governance must be an object")
        else:
            multisig = governance.get("multisig")
            if not isinstance(multisig, dict):
                errors.append("governance.multisig must be an object")
            else:
                _register_name(names, MULTISIG_NAME, "governance.multisig", errors)
                _validate_canister(multisig, "governance.multisig", errors, in_sections=False)
                _check_raw_principals(multisig, "governance.multisig", errors)
            for key, val in governance.items():
                if key == "multisig":
                    continue
                if key == "apply_requires_proposal" and isinstance(val, dict):
                    continue
                if key not in ("apply_requires_proposal",):
                    errors.append(f"governance.{key}: unknown field")

    sections = sheet.get("sections")
    if not isinstance(sections, list):
        errors.append("sections must be a list")
        sections = []

    for si, section in enumerate(sections):
        spath = f"sections[{si}]"
        if not isinstance(section, dict):
            errors.append(f"{spath} must be an object")
            continue
        _check_raw_principals(section, spath, errors)
        if "sync" in section and section["sync"] not in SYNC_MODES:
            errors.append(f"{spath}.sync must be one of {', '.join(SYNC_MODES)}")
        if "commanders" in section:
            _validate_commanders(section["commanders"], f"{spath}.commanders", errors)
        if "stand_template" in section:
            _validate_stand_template(section["stand_template"], spath, names, errors)
        for sj, stand in enumerate(section.get("stands") or []):
            stpath = f"{spath}.stands[{sj}]"
            if not isinstance(stand, dict):
                errors.append(f"{stpath} must be an object")
                continue
            _check_raw_principals(stand, stpath, errors)
            if "sync" in stand and stand["sync"] not in SYNC_MODES:
                errors.append(f"{stpath}.sync must be one of {', '.join(SYNC_MODES)}")
            if "commanders" in stand:
                _validate_commanders(stand["commanders"], f"{stpath}.commanders", errors)
            if "baton" in stand and stand["baton"] is not None:
                _validate_baton(stand["baton"], f"{stpath}.baton", errors, stand)
            for ck, canister in enumerate(stand.get("canisters") or []):
                cpath = f"{stpath}.canisters[{ck}]"
                if not isinstance(canister, dict):
                    errors.append(f"{cpath} must be an object")
                    continue
                cname = canister.get("name")
                if isinstance(cname, str):
                    _register_name(names, cname, cpath, errors)
                _validate_canister(canister, cpath, errors, in_sections=True)
                _check_raw_principals(canister, cpath, errors)
            if isinstance(stand.get("baton"), dict):
                _check_raw_principals(stand["baton"], f"{stpath}.baton", errors)

    if isinstance(conductor, dict):
        backend = conductor.get("backend")
        if isinstance(backend, dict):
            _validate_lockout_conductor(backend.get("controllers"), conductor.get("commanders"), errors)
    _validate_lockout_self_only(sheet, names, errors)

    _validate_domains(sheet.get("domains"), names, errors)
    _validate_env_principals(sheet, errors)

    for target_env in env_targets:
        _validate_placeholders_for_env(sheet, target_env, names, errors)
        if target_env == "production":
            _validate_production_rules(sheet, errors)

    return errors


# ── internal helpers ────────────────────────────────────────────────────────


def _iter_named_canisters(sheet: dict):
    conductor = sheet.get("conductor")
    if isinstance(conductor, dict):
        section = {"name": SYNTHETIC_SECTION_CONDUCTOR}
        stand = {"name": SYNTHETIC_STAND_CONDUCTOR}
        for key in CONDUCTOR_KEYS:
            block = conductor.get(key)
            if isinstance(block, dict):
                yield section, stand, CONDUCTOR_NAMES[key], block
    governance = sheet.get("governance")
    if isinstance(governance, dict):
        multisig = governance.get("multisig")
        if isinstance(multisig, dict):
            section = {"name": SYNTHETIC_SECTION_GOVERNANCE}
            stand = {"name": SYNTHETIC_STAND_GOVERNANCE}
            yield section, stand, MULTISIG_NAME, multisig
    for section in sheet.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for stand in section.get("stands") or []:
            if not isinstance(stand, dict):
                continue
            for canister in stand.get("canisters") or []:
                if isinstance(canister, dict):
                    yield section, stand, canister.get("name", ""), canister


def _register_name(names: dict[str, str], name: str, path: str, errors: list[str]) -> None:
    if not name:
        errors.append(f"{path}.name is required")
        return
    if name in names:
        errors.append(f"{path}.name duplicates canister {name!r} (first at {names[name]})")
    else:
        names[name] = path


def _validate_canister(canister: dict, path: str, errors: list[str], *, in_sections: bool) -> None:
    mode = canister.get("mode", "managed")
    if mode not in MODES:
        errors.append(f"{path}.mode must be one of {sorted(MODES)}")
    kind = canister.get("kind")
    if kind not in KINDS:
        errors.append(f"{path}.kind must be one of {sorted(KINDS)}")
    wasm = canister.get("wasm")
    if not isinstance(wasm, str) or not _is_wasm_ref(wasm):
        errors.append(f"{path}.wasm must be family or family@version")
    upgrade = canister.get("upgrade", "upgrade")
    if upgrade not in UPGRADES:
        errors.append(f"{path}.upgrade must be one of {sorted(UPGRADES)}")
    if upgrade == "reinstall" and not canister.get("allow_destructive"):
        errors.append(f"{path}: upgrade reinstall requires allow_destructive: true")
    if canister.get("retire") is True and not canister.get("allow_destructive"):
        errors.append(f"{path}: retire: true requires allow_destructive: true")
    controllers = canister.get("controllers")
    if not isinstance(controllers, list) or not controllers:
        errors.append(f"{path}.controllers must be a non-empty list")
    elif not all(isinstance(c, str) and c.strip() for c in controllers):
        errors.append(f"{path}.controllers entries must be non-empty strings")
    elif (canister.get("name") or "").endswith("-baton"):
        # A baton exists so that upgrades of its stand need its commanders'
        # approval. Casals as IC controller could reinstall the baton and void
        # that; Casals only ever operates it as `top_commander`. Casals installs
        # the baton at creation and then drops itself (set_controllers, self last).
        if "$self" in controllers:
            errors.append(
                f"{path}.controllers must not include $self: Casals operates a baton as top_commander, "
                "never as IC controller (baton upgrades go through the multisig)"
            )
        # The baton is the only path to upgrade its stand; the one key that can
        # reinstall a broken baton is the orchestra's multisig (the signers).
        if "$multisig" not in controllers:
            errors.append(
                f"{path}.controllers must include $multisig: only the governance multisig "
                "may reinstall a baton (declare governance.multisig)"
            )
    if "commanders" in canister:
        _validate_commanders(canister["commanders"], f"{path}.commanders", errors)
    if "config" in canister:
        if not isinstance(canister["config"], list):
            errors.append(f"{path}.config must be a list")
        else:
            for i, entry in enumerate(canister["config"]):
                _validate_config_entry(entry, f"{path}.config[{i}]", errors)
    if "health" in canister:
        _validate_health(canister["health"], f"{path}.health", errors)
    if "cycles" in canister and isinstance(canister["cycles"], dict):
        _validate_cycles_block(canister["cycles"], f"{path}.cycles", errors)
    if in_sections and not canister.get("name"):
        errors.append(f"{path}.name is required")
    if "content" in canister and not (isinstance(canister["content"], str) and canister["content"].strip()):
        errors.append(f"{path}.content must be a registry.publish path")
    files = canister.get("files")
    if files is not None and not (
        isinstance(files, dict)
        and all(isinstance(k, str) and k.startswith("/") and isinstance(v, str) for k, v in files.items())
    ):
        errors.append(f"{path}.files must map '/key' to text")
    if "content" in canister or files:
        if kind != "frontend":
            errors.append(f"{path}: content/files are for kind frontend")
        if isinstance(controllers, list) and "$self" not in controllers:
            errors.append(f"{path}: content/files need $self among controllers (Casals writes the assets)")
    if "optional" in canister and not isinstance(canister["optional"], bool):
        errors.append(f"{path}.optional must be a boolean")
    if "{n}" in (canister.get("name") or "") and not canister.get("optional"):
        errors.append(f"{path}.name: numbered members ({{n}}) must be optional")


def _validate_config_entry(entry: Any, path: str, errors: list[str]) -> None:
    if not isinstance(entry, dict):
        errors.append(f"{path} must be an object")
        return
    if not isinstance(entry.get("method"), str):
        errors.append(f"{path}.method must be a string")
    if "args" not in entry:
        errors.append(f"{path}.args is required")
    if "converged_when" in entry:
        _validate_converged_when(entry["converged_when"], f"{path}.converged_when", errors)


def _validate_converged_when(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    if not isinstance(value.get("query"), str):
        errors.append(f"{path}.query must be a string")
    if "equals_args" in value and not isinstance(value["equals_args"], bool):
        errors.append(f"{path}.equals_args must be a boolean")
    if "contains" in value and not isinstance(value["contains"], dict):
        errors.append(f"{path}.contains must be an object")
    if sum(k in value for k in ("equals_args", "contains", "equals")) > 1:
        errors.append(f"{path}: use one of equals_args, contains, equals")


def _validate_health(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} must be a list")
        return
    for i, entry in enumerate(value):
        ep = f"{path}[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{ep} must be an object")
            continue
        has_query = "query" in entry
        has_http = "http" in entry
        if has_query == has_http:
            errors.append(f"{ep} must have exactly one of query or http")
            continue
        if has_query:
            if not isinstance(entry.get("query"), str):
                errors.append(f"{ep}.query must be a string")
            if "expect" not in entry:
                errors.append(f"{ep}.expect is required for query health checks")
        if has_http:
            if not isinstance(entry.get("http"), str):
                errors.append(f"{ep}.http must be a string")
            status = entry.get("status")
            if not isinstance(status, int):
                errors.append(f"{ep}.status must be an integer")


def _validate_commanders(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} must be a list")
        return
    for i, entry in enumerate(value):
        ep = f"{path}[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{ep} must be an object")
            continue
        if not isinstance(entry.get("principal"), str):
            errors.append(f"{ep}.principal must be a string")
        if not isinstance(entry.get("permissions"), str):
            errors.append(f"{ep}.permissions must be a string")


def _validate_baton(value: Any, path: str, errors: list[str], stand: dict | None = None) -> None:
    """`baton` is policy only: the baton canister is a regular `-baton` member of the stand."""
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    for field in ("commanders", "threshold", "manages", "hand_off"):
        if field not in value:
            errors.append(f"{path}.{field} is required")
    cmds = value.get("commanders")
    if cmds is not None:
        if not isinstance(cmds, list) or not cmds:
            errors.append(f"{path}.commanders must be a non-empty list")
        else:
            for i, entry in enumerate(cmds):
                ep = f"{path}.commanders[{i}]"
                if isinstance(entry, str):
                    if not entry.strip():
                        errors.append(f"{ep} must be a principal or placeholder")
                elif isinstance(entry, dict):
                    if not (isinstance(entry.get("principal"), str) and entry["principal"].strip()):
                        errors.append(f"{ep}.principal must be a string")
                    w = entry.get("weight", 1)
                    if not isinstance(w, int) or isinstance(w, bool) or w < 1:
                        errors.append(f"{ep}.weight must be a positive integer")
                    for k in entry:
                        if k not in ("principal", "weight"):
                            errors.append(f"{ep}.{k}: unknown field")
                else:
                    errors.append(f"{ep} must be a principal or {{principal, weight}}")
    threshold = value.get("threshold")
    if threshold is not None and (not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1):
        errors.append(f"{path}.threshold must be a positive integer")
    elif isinstance(threshold, int) and isinstance(cmds, list) and cmds:
        total = sum(c["weight"] for c in baton_commanders(value))
        if threshold > total:
            errors.append(f"{path}.threshold {threshold} exceeds the commanders' total weight {total}")
    manages = value.get("manages")
    if manages is not None and manages != "*" and not (
        isinstance(manages, list) and all(isinstance(r, str) and r.strip() for r in manages)
    ):
        errors.append(f"{path}.manages must be a list of member roles or \"*\"")
    hand_off = value.get("hand_off")
    if "hand_off" in value and not (isinstance(hand_off, bool) or hand_off == HAND_OFF_SOLE):
        errors.append(f"{path}.hand_off must be true, false or \"{HAND_OFF_SOLE}\"")
    if stand is not None:
        member = stand_member(stand, "baton")
        if member is None:
            errors.append(f"{path}: stand has no '-baton' canister")
        elif (member.get("install_arg") or {}).get("top_commander") != "$self":
            errors.append(f"{path}: the baton canister's install_arg.top_commander must be $self (Casals configures it)")
        if hand_off == HAND_OFF_SOLE and member is not None:
            # Sole hand-off: after install the baton (plus the member itself, when it
            # keeps `$this`) is the controller. Casals and the deployer would be a
            # way around the baton's approvals, and Casals could no longer write
            # assets into a `content`/`files` frontend.
            for m in baton_manages(stand):
                mname = m.get("name") or "?"
                ctls = m.get("controllers") if isinstance(m.get("controllers"), list) else []
                for banned in ("$self", "$deployer"):
                    if banned in ctls:
                        errors.append(
                            f"{path}: hand_off \"{HAND_OFF_SOLE}\" but managed member {mname} lists {banned} "
                            "among its controllers (the baton, and optionally $this, control it)"
                        )
                if m.get("content") or m.get("files"):
                    errors.append(
                        f"{path}: hand_off \"{HAND_OFF_SOLE}\" but managed member {mname} has content/files "
                        "(Casals writes those as a controller) — leave it out of manages"
                    )


def _validate_stand_template(value: Any, spath: str, names: dict[str, str], errors: list[str]) -> None:
    path = f"{spath}.stand_template"
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    if not isinstance(value.get("name_pattern"), str):
        errors.append(f"{path}.name_pattern must be a string")
    if not isinstance(value.get("created_by"), str):
        errors.append(f"{path}.created_by must be a string")
    canisters = value.get("canisters")
    if not isinstance(canisters, list) or not canisters:
        errors.append(f"{path}.canisters must be a non-empty list")
    else:
        for i, canister in enumerate(canisters):
            cpath = f"{path}.canisters[{i}]"
            if not isinstance(canister, dict):
                errors.append(f"{cpath} must be an object")
                continue
            cname = canister.get("name")
            if isinstance(cname, str) and "*" not in cname:
                _register_name(names, cname, cpath, errors)
            _validate_canister(canister, cpath, errors, in_sections=True)
    if "controllers" in value and not isinstance(value["controllers"], list):
        errors.append(f"{path}.controllers must be a list")
    if "commanders" in value:
        _validate_commanders(value["commanders"], f"{path}.commanders", errors)
    if isinstance(value.get("baton"), dict):
        _validate_baton(value["baton"], f"{path}.baton", errors, value)


def _validate_environments(sheet: dict, env_targets: list[str], errors: list[str]) -> None:
    envs = sheet.get("environments")
    if not isinstance(envs, dict) or not envs:
        errors.append("environments must be a non-empty object")
        return
    for env_name in env_targets:
        block = envs.get(env_name)
        path = f"environments.{env_name}"
        if not isinstance(block, dict):
            errors.append(f"{path} must be an object")
            continue
        if not isinstance(block.get("network"), str):
            errors.append(f"{path}.network is required")
        if "cycles" in block and isinstance(block["cycles"], dict):
            _validate_cycles_block(block["cycles"], f"{path}.cycles", errors)
        # `bindings` names the ids of adopted canisters: code Casals never installs.
        bindings = block.get("bindings", {})
        if not isinstance(bindings, dict):
            errors.append(f"{path}.bindings must be an object")
            continue
        for cname, cid in bindings.items():
            found = find_canister(sheet, cname)
            if not found or not isinstance(cid, str) or not cid.strip():
                errors.append(f"{path}.bindings.{cname}: unknown canister or empty id")
            elif (found[2].get("mode") or "managed") != "adopted":
                errors.append(f"{path}.bindings.{cname}: only adopted canisters take a declared id")


def _validate_registry(sheet: dict, env: str | None, errors: list[str]) -> None:
    registry = sheet.get("registry")
    if not isinstance(registry, dict):
        errors.append("registry must be an object")
        return
    wasms = registry.get("wasms")
    if not isinstance(wasms, list) or not wasms:
        errors.append("registry.wasms must be a non-empty list")
        return
    for i, entry in enumerate(wasms):
        path = f"registry.wasms[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{path} must be an object")
            continue
        for field in ("family", "version", "source"):
            if not isinstance(entry.get(field), str):
                errors.append(f"{path}.{field} is required")
    publish = registry.get("publish", [])
    if not isinstance(publish, list):
        errors.append("registry.publish must be a list")
        return
    published = set()
    for i, entry in enumerate(publish):
        path = f"registry.publish[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{path} must be an object")
            continue
        for field in ("path", "source"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"{path}.{field} is required")
        if entry.get("path", "").startswith(WASM_NAMESPACE + "/"):
            errors.append(f"{path}.path may not start with '{WASM_NAMESPACE}/'")
        pin = entry.get("sha256")
        if pin is not None and not _is_hex64(pin):
            errors.append(f"{path}.sha256 must be a 64-hex bundle hash (docs/BUNDLES.md)")
        published.add(entry.get("path"))
    pinned = {e.get("path") for e in publish if isinstance(e, dict) and e.get("sha256")}
    for section, stand, name, canister in iter_canisters(sheet):
        content = canister.get("content")
        if not content:
            continue
        if content not in published:
            errors.append(f"canister {name}: content '{content}' has no registry.publish entry")
        elif content not in pinned and sync_mode(section, stand) == SYNC_MANUAL:
            # Nothing reconciles a manual frontend routinely, so the pin is the
            # only statement of what it should serve (#51).
            errors.append(f"canister {name}: content '{content}' must be pinned (registry.publish sha256) "
                          f"because its stand/section is sync: manual")


def _validate_cycles_block(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    numeric_keys = (
        "min_balance_tc", "budget_tc", "conductor_min_balance_tc",
    )
    for key in numeric_keys:
        if key in value and not isinstance(value[key], (int, float)):
            errors.append(f"{path}.{key} must be a number")
    if "reuse_pool" in value and not isinstance(value["reuse_pool"], bool):
        errors.append(f"{path}.reuse_pool must be a boolean")


def _validate_domains(value: Any, names: dict[str, str], errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append("domains must be a list")
        return
    for i, entry in enumerate(value):
        path = f"domains[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{path} must be an object")
            continue
        if not isinstance(entry.get("host"), str):
            errors.append(f"{path}.host must be a string")
        target = entry.get("canister")
        if not isinstance(target, str):
            errors.append(f"{path}.canister must be a string")
        elif target not in names:
            errors.append(f"{path}.canister refers to unknown canister {target!r}")


def _check_raw_principals(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str) and _looks_like_raw_principal(value):
        errors.append(
            f"{path}: raw principal {value!r} belongs in environments.<env>.principals"
        )
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_raw_principals(v, f"{path}.{k}", errors)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _check_raw_principals(item, f"{path}[{i}]", errors)


def _looks_like_raw_principal(value: str) -> bool:
    """True when ``value`` is in IC principal text form: dash-separated groups of
    five base32 chars and a 2–3 char tail (`rrkah-fqaaa-aaaaa-aaaaq-cai`, `aaaaa-aa`)."""
    segments = value.strip().lower().split("-")
    if len(segments) < 2:
        return False
    body, last = segments[:-1], segments[-1]
    alphabet = set("abcdefghijklmnopqrstuvwxyz234567")
    return (all(len(seg) == 5 and set(seg) <= alphabet for seg in body)
            and 2 <= len(last) <= 3 and set(last) <= alphabet)


def _validate_lockout_conductor(controllers: Any, commanders: Any, errors: list[str]) -> None:
    """Someone other than Casals itself must be able to operate the conductor once the
    sheet is applied: a principal controller, or (when only the multisig controls it)
    a declared conductor commander who can call plan/apply."""
    if not isinstance(controllers, list):
        return
    principal = any(c == "$deployer" or (isinstance(c, str) and c.startswith("$principal:")) for c in controllers)
    if not principal and "$multisig" not in controllers:
        errors.append("conductor.backend.controllers must include $multisig, $principal:*, or $deployer")
    elif not principal and not commanders:
        errors.append(
            "conductor.backend is controlled only by $multisig and conductor.commanders is empty: "
            "after apply nobody but the multisig could call plan/apply"
        )


def _validate_lockout_self_only(sheet: dict, names: dict[str, str], errors: list[str]) -> None:
    for section, stand, cname, canister in _iter_named_canisters(sheet):
        controllers = canister.get("controllers")
        if not isinstance(controllers, list) or len(controllers) != 1:
            continue
        only = controllers[0]
        path = names.get(cname, cname)
        if only == f"$canister:{cname}" or only == "$this":
            errors.append(f"{path}.controllers must not consist only of itself")
        elif only == "$self" and cname == CONDUCTOR_NAMES["backend"]:
            errors.append(f"{path}.controllers must not be only $self")


def _validate_placeholders_for_env(
    sheet: dict, env: str, names: dict[str, str], errors: list[str]
) -> None:
    env_data = env_block(sheet, env)
    principals = env_data.get("principals") or {}
    if not isinstance(principals, dict):
        principals = {}

    def check(value: Any, path: str, stand: dict | None, in_canister: bool = False) -> None:
        if isinstance(value, str):
            for token in find_placeholders(value):
                _check_placeholder(
                    token, path, env, names, principals, env_data, stand, errors, in_canister
                )
        elif isinstance(value, dict):
            for k, v in value.items():
                if _is_comment_key(k):
                    continue
                check(v, f"{path}.{k}", stand, in_canister)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                check(item, f"{path}[{i}]", stand, in_canister)

    # Conductor and governance blocks are yielded by _iter_named_canisters too
    # (on their synthetic stands), so this is the one walk over every canister.
    for section, stand, cname, canister in _iter_named_canisters(sheet):
        base = names.get(cname, cname)
        _walk_for_placeholders(canister, base, stand, check)
    conductor = sheet.get("conductor")
    if isinstance(conductor, dict):
        for field in ("commanders", "settings"):
            if field in conductor:
                check(conductor[field], f"conductor.{field}", None)
    if isinstance(sheet.get("domains"), list):
        check(sheet["domains"], "domains", None)
    if isinstance(sheet.get("cycles"), dict):
        check(sheet["cycles"], "cycles", None)
    for si, section in enumerate(sheet.get("sections") or []):
        if not isinstance(section, dict):
            continue
        if "commanders" in section:
            check(section["commanders"], f"sections[{si}].commanders", None)
        for sj, stand in enumerate(section.get("stands") or []):
            if not isinstance(stand, dict):
                continue
            stpath = f"sections[{si}].stands[{sj}]"
            if "commanders" in stand:
                check(stand["commanders"], f"{stpath}.commanders", stand)
            if isinstance(stand.get("baton"), dict):
                check(stand["baton"], f"{stpath}.baton", stand)
        if isinstance(section.get("stand_template"), dict):
            tmpl = section["stand_template"]
            # `$stand.<role>` inside the template refers to the rendered stand, with every member present.
            sample = instantiate_template_stand(tmpl, "stand", [c.get("name", "").replace("{n}", "1") for c in tmpl.get("canisters") or [] if isinstance(c, dict)])
            for i, canister in enumerate(tmpl.get("canisters") or []):
                if isinstance(canister, dict):
                    _walk_for_placeholders(
                        canister, f"sections[{si}].stand_template.canisters[{i}]", sample, check
                    )
            if isinstance(tmpl.get("baton"), dict):
                check(tmpl["baton"], f"sections[{si}].stand_template.baton", sample)
            for field in ("created_by", "controllers"):
                if field in tmpl:
                    check(tmpl[field], f"sections[{si}].stand_template.{field}", None)
            if "commanders" in tmpl:
                check(tmpl["commanders"], f"sections[{si}].stand_template.commanders", sample)


def _is_comment_key(key: Any) -> bool:
    """`$comment` (and `$comment_*`) entries are prose, not configuration."""
    return isinstance(key, str) and key.startswith("$comment")


def _walk_for_placeholders(obj: Any, path: str, stand: dict | None, check) -> None:
    """Placeholders inside one canister block (`$this` is legal only here)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _is_comment_key(k):
                continue
            check(v, f"{path}.{k}", stand, True)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check(v, f"{path}[{i}]", stand, True)
    else:
        check(obj, path, stand, True)


def _check_placeholder(
    token: str,
    path: str,
    env: str,
    names: dict[str, str],
    principals: dict,
    env_data: dict,
    stand: dict | None,
    errors: list[str],
    in_canister: bool = False,
) -> None:
    if token == "$deployer":
        if env == "production" and "$deployer" not in principals.values():
            errors.append(
                f"{path}: $deployer is not allowed in production "
                "(unless listed in environments.production.principals)"
            )
        return
    if token == "$this":
        if not in_canister:
            errors.append(f"{path}: $this (the canister's own id) is only meaningful inside a canister block")
        return
    if token.startswith("$principal:"):
        alias = token.split(":", 1)[1]
        if alias not in principals:
            errors.append(
                f"{path}: {token} unresolved in environments.{env}.principals"
            )
        elif is_code_checksum(principals[alias]) and not _is_commander_principal_path(path):
            errors.append(
                f"{path}: {token} is an access-code checksum in environments.{env}.principals; "
                "it may only be used as a commanders[].principal"
            )
        return
    if token.startswith("$canister:"):
        target = token.split(":", 1)[1]
        if target not in names:
            errors.append(f"{path}: {token} refers to unknown canister {target!r}")
        return
    if token.startswith("$stand."):
        role = token.split(".", 1)[1]
        if stand is None:
            errors.append(f"{path}: {token} requires a stand context")
        elif stand_member(stand, role) is None:
            errors.append(f"{path}: {token} has no matching canister in stand")
        return
    if token.startswith("$env."):
        key = token[5:]
        if _env_get(env_data, key) is _MISSING:
            errors.append(
                f"{path}: {token} unresolved in environments.{env}"
            )
        return


_MISSING = object()


def _is_commander_principal_path(path: str) -> bool:
    """True for ``….commanders[<i>].principal`` — the only place an access-code
    alias may appear (baton ``commanders`` are bare principals and do not qualify)."""
    head, sep, tail = path.rpartition(".")
    if sep != "." or tail != "principal":
        return False
    base, br, idx = head.rpartition("[")
    return (br == "[" and idx.endswith("]") and idx[:-1].isdigit()
            and base.endswith(".commanders") and not base.endswith(".baton.commanders"))


def _validate_env_principals(sheet: dict, errors: list[str]) -> None:
    """``environments.<env>.principals`` values are principals, placeholders,
    or well-formed access-code checksums (``sha256:<64 hex>``)."""
    envs = sheet.get("environments")
    if not isinstance(envs, dict):
        return
    for env, data in envs.items():
        principals = data.get("principals") if isinstance(data, dict) else None
        if not isinstance(principals, dict):
            continue
        for alias, value in principals.items():
            if is_code_checksum(value):
                try:
                    normalize_code_checksum(value)
                except ValueError as exc:
                    errors.append(f"environments.{env}.principals.{alias}: {exc}")


def _env_get(data: dict, dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _validate_production_rules(sheet: dict, errors: list[str]) -> None:
    registry = sheet.get("registry") or {}
    for i, entry in enumerate(registry.get("wasms") or []):
        if isinstance(entry, dict) and not entry.get("sha256"):
            errors.append(f"registry.wasms[{i}].sha256 is required for production")
    for i, entry in enumerate(registry.get("publish") or []):
        if isinstance(entry, dict) and not entry.get("sha256"):
            errors.append(f"registry.publish[{i}].sha256 (bundle hash) is required for production")


def _env_lookup_root(sheet: dict, env: str, ctx: ResolveContext) -> dict:
    if ctx.env_values:
        return ctx.env_values
    return env_block(sheet, env)


def _resolve_token(
    token: str,
    path: str,
    stand: dict | None,
    env_data: dict,
    ctx: ResolveContext,
    this: str | None = None,
) -> Any:
    token = _unbrace(token)
    if token == "$multisig":
        cid = ctx.canister_ids.get(MULTISIG_NAME)
        if not cid:
            raise UnresolvedPlaceholder(token, path)
        return cid
    if token == "$this":
        # The canister's own id; a template member keeps the token until rendered.
        if stand is TEMPLATE_STAND:
            return token
        cid = ctx.canister_ids.get(this or "") if this else None
        if not cid:
            raise UnresolvedPlaceholder(token, path)
        return cid
    if token == "$self":
        if not ctx.self_id:
            raise UnresolvedPlaceholder(token, path)
        return ctx.self_id
    if token == "$deployer":
        if not ctx.deployer:
            raise UnresolvedPlaceholder(token, path)
        return ctx.deployer
    if token.startswith("$canister:"):
        name = token.split(":", 1)[1]
        cid = ctx.canister_ids.get(name)
        if not cid:
            raise UnresolvedPlaceholder(token, path)
        return cid
    if token.startswith("$stand."):
        if stand is TEMPLATE_STAND:
            return token  # a template is rendered per stand later; keep the token
        if stand is None:
            raise UnresolvedPlaceholder(token, path)
        role = token.split(".", 1)[1]
        member = stand_member(stand, role)
        if not member:
            raise UnresolvedPlaceholder(token, path)
        cid = ctx.canister_ids.get(member.get("name", ""))
        if not cid:
            raise UnresolvedPlaceholder(token, path)
        return cid
    if token.startswith("$principal:"):
        alias = token.split(":", 1)[1]
        principals = env_data.get("principals") if isinstance(env_data, dict) else None
        if not isinstance(principals, dict) or alias not in principals:
            raise UnresolvedPlaceholder(token, path)
        val = principals[alias]
        if isinstance(val, str) and val.startswith("$"):
            return _resolve_token(val, path, stand, env_data, ctx)
        return val
    if token.startswith("$env."):
        key = token[5:]
        val = _env_get(env_data, key)
        if val is _MISSING:
            raise UnresolvedPlaceholder(token, path)
        if isinstance(val, str):  # an env value may itself hold placeholders (a portal URL with a canister id)
            for inner in find_placeholder_tokens(val):
                val = val.replace(inner, _as_text(_resolve_token(inner, path, stand, env_data, ctx)))
        return val
    raise UnresolvedPlaceholder(token, path)


def _resolve_canister_tree(canister: dict, path: str, stand: dict | None, resolve_value, this: str | None) -> None:
    for key, val in list(canister.items()):
        canister[key] = resolve_value(val, f"{path}.{key}", stand, this)


def apply_requires_proposal(sheet: dict, env: str) -> bool:
    """`governance.apply_requires_proposal`: a bool, or `{<env>: bool, default: bool}`."""
    v = (sheet.get("governance") or {}).get("apply_requires_proposal", False)
    if isinstance(v, dict):
        v = v.get(env, v.get("default", False))
    return bool(v)


def glob_match(name: str, pattern: str) -> bool:
    """`*` wildcard match (stand_template.name_pattern); no other glob syntax."""
    parts = pattern.split("*")
    if len(parts) == 1:
        return name == pattern
    if not name.startswith(parts[0]) or not name.endswith(parts[-1]):
        return False
    pos = len(parts[0])
    for part in parts[1:-1]:
        idx = name.find(part, pos)
        if idx < 0:
            return False
        pos = idx + len(part)
    return pos <= len(name) - len(parts[-1])


def template_member_match(template_name: str, member: str, stand_name: str) -> dict | None:
    """Does `member` (given as `{stand}-token`, `e2e-token`, `{stand}-quarter-3`, …)
    name the template canister `template_name`? Returns the substitutions
    (`{"n": "3"}` for numbered members) or None."""
    pattern = template_name.replace("{stand}", stand_name)
    member = member.replace("{stand}", stand_name)
    if "{n}" not in pattern:
        return {} if member == pattern else None
    prefix, suffix = pattern.split("{n}", 1)
    middle = member[len(prefix):len(member) - len(suffix)] if suffix else member[len(prefix):]
    if member.startswith(prefix) and member.endswith(suffix) and middle.isdigit():
        return {"n": middle}
    return None


def template_members(template: dict, stand_name: str, members: list[str] | None) -> list[tuple[dict, dict]]:
    """The template canisters a stand gets, as `(canister, substitutions)`:
    every required one, plus each `optional: true` one named in `members`
    (numbered members — `{n}` in the template name — once per number)."""
    out: list[tuple[dict, dict]] = []
    for c in template.get("canisters") or []:
        if not isinstance(c, dict):
            continue
        if not c.get("optional"):
            out.append((c, {}))
            continue
        chosen: dict[str, dict] = {}
        for m in members or []:
            subs = template_member_match(c.get("name", ""), m, stand_name)
            if subs is not None:
                chosen[subs.get("n", "")] = subs
        out.extend((c, chosen[n]) for n in sorted(chosen, key=lambda n: int(n or 0)))
    return out


def unknown_members(template: dict, stand_name: str, members: list[str]) -> list[str]:
    """Members that name no `optional: true` template canister."""
    optional = [c for c in template.get("canisters") or [] if isinstance(c, dict) and c.get("optional")]
    return [m for m in members
            if not any(template_member_match(c.get("name", ""), m, stand_name) is not None for c in optional)]


def instantiate_template_stand(template: dict, stand_name: str, members: list[str] | None = None) -> dict:
    """A `stand_template` rendered for one stand: `{stand}` (and `{n}` for
    numbered members) substituted in every string of each canister — names,
    install args, files. `optional: true` canisters are kept only when named
    in `members`."""
    canisters = []
    for c, subs in template_members(template, stand_name, members):
        text = json.dumps({k: v for k, v in c.items() if k != "optional"})
        for key, val in subs.items():
            text = text.replace("{" + key + "}", val)
        canisters.append(json.loads(text))
    spec = {k: v for k, v in template.items() if k in ("commanders", "baton")}
    spec = json.loads(json.dumps(spec).replace("{stand}", stand_name))
    spec["canisters"] = [json.loads(json.dumps(c).replace("{stand}", stand_name)) for c in canisters]
    spec["name"] = stand_name
    return spec


def materialize(sheet: dict, live_stands: dict[str, dict]) -> dict:
    """Copy of the sheet where every live stand (name → {section, members})
    matching a section's `stand_template` is a declared stand, and the
    template's `created_by` holds `stand.create` on the section. Planner,
    oracle and `show` all reason about this one declared world."""
    out = json.loads(json.dumps(sheet))
    for section in out.get("sections") or []:
        tmpl = section.get("stand_template")
        if not isinstance(tmpl, dict):
            continue
        if tmpl.get("created_by"):
            section["commanders"] = [*(section.get("commanders") or []),
                                     {"principal": tmpl["created_by"], "permissions": ["stand.create"]}]
        stands = section.setdefault("stands", [])
        declared = {st.get("name") for st in stands}
        for name, live in sorted(live_stands.items()):
            if live.get("section") == section.get("name") and name not in declared \
                    and glob_match(name, tmpl.get("name_pattern") or ""):
                stand = instantiate_template_stand(tmpl, name, live.get("members"))
                # The mint is the act (#51): a stand still being built is
                # reconciled even under a `sync: manual` section; once the
                # conductor has found it converged it follows the section.
                if sync_mode(section) == SYNC_MANUAL and not live.get("built", True):
                    stand["sync"] = SYNC_AUTO
                stands.append(stand)
    return out
