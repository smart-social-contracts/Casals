"""Sheet v2 — pure validation, placeholder resolution, and navigation helpers.

Standard library only; safe to import from the Basilisk canister and the CLI.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from util import PRINCIPAL_RE

SCHEMA_VERSION = 2

MODES = frozenset({"managed", "adopted"})
KINDS = frozenset({"backend", "frontend"})
UPGRADES = frozenset({"upgrade", "reinstall"})
STAND_ROLES = frozenset({"backend", "frontend", "baton"})
CONDUCTOR_KEYS = ("backend", "frontend", "file_registry", "file_registry_frontend")
CONDUCTOR_NAMES = {
    "backend": "casals-backend",
    "frontend": "casals-frontend",
    "file_registry": "file-registry",
    "file_registry_frontend": "file-registry-frontend",
}
SYNTHETIC_SECTION_CONDUCTOR = "Casals"
SYNTHETIC_STAND_CONDUCTOR = "conductor"
SYNTHETIC_SECTION_GOVERNANCE = "System"
SYNTHETIC_STAND_GOVERNANCE = "governance"
MULTISIG_NAME = "multisig"
WASM_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*(@[a-zA-Z0-9][a-zA-Z0-9._-]*)?$")

PLACEHOLDER_RE = re.compile(
    r"\$(?:multisig|self|deployer|canister:[a-zA-Z0-9._-]+|"
    r"stand\.(?:backend|frontend|baton)|principal:[a-zA-Z0-9._-]+|"
    r"env(?:\.[a-zA-Z0-9_]+)+)"
)


class UnresolvedPlaceholder(Exception):
    """A placeholder could not be resolved."""

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        super().__init__(f"unresolved placeholder {name!r} at {path}")


@dataclass
class ResolveContext:
    """Runtime bindings used when resolving placeholders."""

    deployer: str | None
    self_id: str | None
    canister_ids: dict[str, str]
    env_values: dict[str, Any]


def find_placeholders(value: Any) -> set[str]:
    """Return every placeholder token found in nested dict/list/str values."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            found.update(find_placeholders(v))
    elif isinstance(value, list):
        for item in value:
            found.update(find_placeholders(item))
    return found


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


def stand_member(stand: dict, role: str) -> dict | None:
    """Return the canister in ``stand`` whose kind or name suffix matches ``role``."""
    role = (role or "").strip().lower()
    if role not in STAND_ROLES:
        return None
    canisters = [c for c in (stand.get("canisters") or []) if isinstance(c, dict)]
    if role == "baton":
        for canister in canisters:
            if canister.get("name", "").endswith("-baton"):
                return canister
        return None
    matches: list[dict] = []
    for canister in canisters:
        cname = canister.get("name", "")
        if cname.endswith("-baton"):
            continue
        if canister.get("kind") == role:
            matches.append(canister)
        elif role == "backend" and cname.endswith("-backend"):
            matches.append(canister)
        elif role == "frontend" and cname.endswith("-frontend"):
            matches.append(canister)
    return matches[0] if matches else None


def iter_canisters(sheet: dict) -> Iterator[tuple[dict, dict, dict]]:
    """Yield ``(section, stand, canister)`` for every declared canister.

    Conductor entries appear under synthetic section ``Casals`` / stand
    ``conductor``; governance ``multisig`` under ``System`` / ``governance``.
    """
    for section, stand, _name, canister in _iter_named_canisters(sheet):
        yield section, stand, canister


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

    def resolve_value(value: Any, path: str, stand: dict | None) -> Any:
        if isinstance(value, dict):
            return {k: resolve_value(v, f"{path}.{k}", stand) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_value(v, f"{path}[{i}]", stand) for i, v in enumerate(value)]
        if not isinstance(value, str):
            return value
        tokens = PLACEHOLDER_RE.findall(value)
        if not tokens:
            return value
        if len(tokens) == 1 and value == tokens[0]:
            try:
                return _resolve_token(tokens[0], path, stand, env_data, ctx)
            except UnresolvedPlaceholder as exc:
                if partial:
                    unresolved.add(exc.name)
                    return value
                raise
        out = value
        for token in tokens:
            try:
                replacement = _resolve_token(token, path, stand, env_data, ctx)
            except UnresolvedPlaceholder as exc:
                if partial:
                    unresolved.add(exc.name)
                    continue
                raise
            if not isinstance(replacement, (str, int, float, bool)) and replacement is not None:
                raise UnresolvedPlaceholder(token, path)
            out = out.replace(token, str(replacement))
        return out

    copied = copy.deepcopy(sheet)
    for section, stand, _name, canister in _iter_named_canisters(copied):
        _resolve_canister_tree(canister, "canister", stand, resolve_value)
    if isinstance(copied.get("conductor"), dict):
        for key in CONDUCTOR_KEYS:
            block = copied["conductor"].get(key)
            if isinstance(block, dict):
                _resolve_canister_tree(block, f"conductor.{key}", None, resolve_value)
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
        _resolve_canister_tree(gov["multisig"], "governance.multisig", None, resolve_value)
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
                    _resolve_canister_tree(
                        canister, f"{spath}.stand_template.canisters[{j}]", None, resolve_value
                    )
            if "controllers" in tmpl:
                tmpl["controllers"] = resolve_value(
                    tmpl["controllers"], f"{spath}.stand_template.controllers", None
                )
            if "commanders" in tmpl:
                tmpl["commanders"] = resolve_value(
                    tmpl["commanders"], f"{spath}.stand_template.commanders", None
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
                stand["commanders"] = resolve_value(stand["commanders"], f"{stpath}.commanders", None)
            if isinstance(stand.get("baton"), dict):
                stand["baton"] = resolve_value(stand["baton"], f"{stpath}.baton", stand)
            for k, canister in enumerate(stand.get("canisters") or []):
                if isinstance(canister, dict):
                    _resolve_canister_tree(
                        canister, f"{stpath}.canisters[{k}]", stand, resolve_value
                    )
    return copied, unresolved


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
            if not isinstance(block, dict):
                errors.append(f"{path} must be an object")
                continue
            cname = CONDUCTOR_NAMES[key]
            _register_name(names, cname, path, errors)
            _validate_canister(block, path, errors, in_sections=False)
            _check_raw_principals(block, path, errors)

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
            if "commanders" in stand:
                _validate_commanders(stand["commanders"], f"{stpath}.commanders", errors)
            if "baton" in stand and stand["baton"] is not None:
                _validate_baton(stand["baton"], f"{stpath}.baton", errors)
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
            _validate_lockout_conductor(backend.get("controllers"), errors)
    _validate_lockout_self_only(sheet, names, errors)

    _validate_domains(sheet.get("domains"), names, errors)

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
    if not isinstance(wasm, str) or not WASM_REF_RE.match(wasm):
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


def _validate_baton(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    if not isinstance(value.get("wasm"), str) or not WASM_REF_RE.match(value["wasm"]):
        errors.append(f"{path}.wasm must be family or family@version")
    for field in ("top_commander", "threshold", "manages", "hand_off"):
        if field not in value:
            errors.append(f"{path}.{field} is required")


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
        _validate_baton(value["baton"], f"{path}.baton", errors)


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
        if env == "production" and not entry.get("sha256"):
            errors.append(f"{path}.sha256 is required for production")


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
    if "sweep_on_retire" in value and not isinstance(value["sweep_on_retire"], bool):
        errors.append(f"{path}.sweep_on_retire must be a boolean")


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
    """True when ``value`` looks like a literal IC principal/canister id."""
    text = value.strip()
    if not text or text.startswith("$") or "@" in text:
        return False
    if not PRINCIPAL_RE.match(text):
        return False
    segments = text.split("-")
    if len(segments) >= 5:
        return True
    if len(segments) <= 3 and len(text) < 30:
        return False
    return len(text) >= 27


def _validate_lockout_conductor(controllers: Any, errors: list[str]) -> None:
    if not isinstance(controllers, list):
        return
    allowed = False
    for item in controllers:
        if item == "$multisig" or item == "$deployer":
            allowed = True
        elif isinstance(item, str) and item.startswith("$principal:"):
            allowed = True
    if not allowed:
        errors.append(
            "conductor.backend.controllers must include at least one of "
            "$multisig, $principal:*, or $deployer"
        )


def _validate_lockout_self_only(sheet: dict, names: dict[str, str], errors: list[str]) -> None:
    for section, stand, cname, canister in _iter_named_canisters(sheet):
        controllers = canister.get("controllers")
        if not isinstance(controllers, list) or len(controllers) != 1:
            continue
        only = controllers[0]
        path = names.get(cname, cname)
        if only == f"$canister:{cname}":
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

    def check(value: Any, path: str, stand: dict | None) -> None:
        if isinstance(value, str):
            for token in find_placeholders(value):
                _check_placeholder(
                    token, path, env, names, principals, env_data, stand, errors
                )
        elif isinstance(value, dict):
            for k, v in value.items():
                check(v, f"{path}.{k}", stand)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                check(item, f"{path}[{i}]", stand)

    for section, stand, cname, canister in _iter_named_canisters(sheet):
        base = names.get(cname, cname)
        _walk_for_placeholders(canister, base, stand, check)
    conductor = sheet.get("conductor")
    if isinstance(conductor, dict):
        for key in CONDUCTOR_KEYS:
            block = conductor.get(key)
            if isinstance(block, dict):
                _walk_for_placeholders(block, f"conductor.{key}", None, check)
        for field in ("commanders", "settings"):
            if field in conductor:
                check(conductor[field], f"conductor.{field}", None)
    governance = sheet.get("governance")
    if isinstance(governance, dict) and isinstance(governance.get("multisig"), dict):
        _walk_for_placeholders(governance["multisig"], "governance.multisig", None, check)
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
                check(stand["commanders"], f"{stpath}.commanders", None)
            if isinstance(stand.get("baton"), dict):
                check(stand["baton"], f"{stpath}.baton", stand)
        if isinstance(section.get("stand_template"), dict):
            tmpl = section["stand_template"]
            for i, canister in enumerate(tmpl.get("canisters") or []):
                if isinstance(canister, dict):
                    _walk_for_placeholders(
                        canister, f"sections[{si}].stand_template.canisters[{i}]", None, check
                    )
            for field in ("created_by", "controllers", "commanders"):
                if field in tmpl:
                    check(tmpl[field], f"sections[{si}].stand_template.{field}", None)


def _walk_for_placeholders(obj: Any, path: str, stand: dict | None, check) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            check(v, f"{path}.{k}", stand)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check(v, f"{path}[{i}]", stand)
    else:
        check(obj, path, stand)


def _check_placeholder(
    token: str,
    path: str,
    env: str,
    names: dict[str, str],
    principals: dict,
    env_data: dict,
    stand: dict | None,
    errors: list[str],
) -> None:
    if token == "$deployer":
        if env == "production" and "$deployer" not in principals.values():
            errors.append(
                f"{path}: $deployer is not allowed in production "
                "(unless listed in environments.production.principals)"
            )
        return
    if token.startswith("$principal:"):
        alias = token.split(":", 1)[1]
        if alias not in principals:
            errors.append(
                f"{path}: {token} unresolved in environments.{env}.principals"
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
        elif role not in STAND_ROLES:
            errors.append(f"{path}: {token} has unsupported stand role")
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
) -> Any:
    if token == "$multisig":
        cid = ctx.canister_ids.get(MULTISIG_NAME)
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
        return val
    raise UnresolvedPlaceholder(token, path)


def _resolve_canister_tree(canister: dict, path: str, stand: dict | None, resolve_value) -> None:
    for key, val in list(canister.items()):
        canister[key] = resolve_value(val, f"{path}.{key}", stand)
