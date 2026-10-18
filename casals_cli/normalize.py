"""Sheet → the document `orchestra_up` builds an orchestra from.

Everything the conductor should not have to know lives here: environments,
principal aliases, permission shorthands, multisig and baton set-up (turned
into plain `config` calls), and `install_arg` objects (turned into Candid).
Placeholders that only exist once canisters do (`$this`, `$canister:<name>`,
`$stand.<role>` in templates, `$self`) are left for the conductor.
"""

from __future__ import annotations

import json

from casals_cli.sheetv2 import (
    CONDUCTOR_NAMES, MULTISIG_NAME, WASM_NAMESPACE, ResolveContext, baton_commanders, env_block, env_monitor,
    find_placeholder_tokens, registry_path, resolve_partial, section_arrangement, stand_member, store_key,
    strip_comments,
)

KEYS = ("canister.create", "canister.deploy", "canister.config", "canister.lifecycle", "canister.delete",
        "canister.call", "canister.browse", "cycles.top_up", "stand.create", "stand.edit", "stand.delete",
        "section.edit", "grant.assign", "wasm.upload", "wasm.authorize", "orchestra.settings", "alias.manage")
RENAMED = {"canister.snapshot": "canister.lifecycle", "canister.revert": "canister.lifecycle",
           "canister.topup": "cycles.top_up", "canister.rename": "canister.config", "canister.tag": "canister.config",
           "stand.rename": "stand.edit", "arrangement.edit": "section.edit", "commander.assign": "grant.assign",
           "subnet.whitelist": "orchestra.settings", "settings.general": "orchestra.settings",
           "settings.cycles": "orchestra.settings", "settings.monitor": "orchestra.settings"}
DROPPED = {"canister.shell", "notification.manage"}
BATON_CAPS = ["propose:managed_upgrade", "submit_approval:managed_upgrade", "execute:managed_upgrade",
              "read_cycle_balance"]
TC = 1_000_000_000_000


def permissions(value) -> list[str]:
    """`"*"`, a comma list or a list of keys (old names and `prefix.*` included) → new keys."""
    if value is None or value == "*":
        return ["*"]
    items = value.split(",") if isinstance(value, str) else list(value)
    out = set()
    for item in (i.strip() for i in items if i.strip()):
        if item == "*":
            return ["*"]
        if item.endswith(".*"):
            out |= {k for k in KEYS if k.startswith(item[:-1])} | {RENAMED[k] for k in RENAMED if k.startswith(item[:-1])}
        elif item not in DROPPED:
            out.add(RENAMED.get(item, item))
    return sorted(out)


def grants(scope: str, commanders) -> list[dict]:
    out = []
    for entry in commanders or []:
        principal = entry.get("principal") if isinstance(entry, dict) else entry
        perms = permissions(entry.get("permissions") if isinstance(entry, dict) else None)
        out.append({"scope": scope, "principal": principal, "permissions": perms, "calls": []})
    return out


def install_arg(value):
    if isinstance(value, dict) and value.get("top_commander"):
        return f'(record {{ top_commander = principal "{value["top_commander"]}" }})'
    return value or None


def text_call(method: str, arg, canister: str = "") -> dict:
    call = {"method": method, "arg": arg if isinstance(arg, str) else json.dumps(arg), "text": True}
    return {**call, "canister": canister} if canister else call


def manages(baton: dict, name: str) -> bool:
    roles = baton.get("manages")
    if roles == "*":
        return not name.endswith("-baton")
    return any(name.endswith(f"-{r}") or f"-{r}-" in name for r in roles or [])


def baton_config(baton: dict, store_id: str) -> list[dict]:
    calls = [text_call("set_config", {"wasm_store_canister_id": store_id})]
    calls += [text_call("add_commander", {**c, "capabilities": BATON_CAPS}) for c in baton_commanders(baton)]
    return calls + [text_call("set_config", {"upgrade_approval_policy": {"threshold": int(baton.get("threshold") or 1)}})]


def canister(spec: dict, keys: dict, baton: dict | None, baton_ref: str, store_id: str) -> dict:
    name = spec["name"]
    config = [text_call(c["method"], c.get("args", "")) for c in spec.get("config") or []]
    config += [{"method": "grant_permission", "arg": f'(record {{ to_principal = principal "{g["principal"]}"; '
                f'permission = variant {{ {g.get("permission", "Commit")} }} }})'} for g in spec.get("grants") or []]
    controllers = list(spec.get("controllers") or [])
    if baton and baton.get("hand_off") and manages(baton, name):
        controllers += [baton_ref] * (baton_ref not in controllers)
        config.append(text_call("add_managed_canister", "$this", baton_ref))
    if baton and name.endswith("-baton"):
        config = baton_config(baton, store_id) + config
    out = {"name": name, "kind": spec.get("kind", "backend"), "wasm": keys.get(spec.get("wasm", ""), spec.get("wasm", "")),
           "public": bool(spec.get("public")), "install_arg": install_arg(spec.get("install_arg")),
           "content": spec.get("content", ""), "files": spec.get("files") or {}, "controllers": controllers,
           "config": config, **({"optional": True} if spec.get("optional") else {})}
    if spec.get("mode") == "adopted":
        out.update(canister_id=spec["canister_id"], adopted=True, status="installed")
    return out


def bind_stand_roles(stand: dict) -> dict:
    """`$stand.<role>` in a declared stand → `$canister:<member>` (the conductor only knows names)."""
    text = json.dumps(stand)
    for token in set(find_placeholder_tokens(text)):
        member = stand_member(stand, token[7:]) if token.startswith("$stand.") else None
        if member:
            text = text.replace(token, f"$canister:{member['name']}")
    return json.loads(text)


def section(sec: dict, keys: dict, store_id: str, adopted: dict) -> dict:
    out = {"name": sec["name"], "description": sec.get("description", ""), "subnet": sec.get("subnet", ""), "stands": []}
    tmpl = section_arrangement(sec)
    if tmpl:
        baton = tmpl.get("baton")
        out["template"] = {"canisters": [canister(c, keys, baton, "$stand.baton", store_id) for c in tmpl["canisters"]],
                           "commanders": [{"principal": g["principal"], "permissions": g["permissions"]}
                                          for g in grants("", tmpl.get("commanders"))]}
    for st in sec.get("stands") or []:
        st = bind_stand_roles(st)
        baton = st.get("baton")
        member = stand_member(st, "baton") if baton else None
        ref = f"$canister:{member['name']}" if member else ""
        rows = [canister({**c, "canister_id": adopted.get(c["name"], "")}, keys, baton, ref, store_id)
                for c in st.get("canisters") or [] if not c.get("retire")]
        out["stands"].append({"name": st["name"], "description": st.get("description", ""),
                              "subnet": st.get("subnet", ""), "canisters": rows})
    return out


def wasm_rows(sheet: dict, uploads: list[dict]) -> tuple[list[dict], dict]:
    sha = {(u["family"], u["version"]): u["sha256"] for u in uploads}
    rows, keys = [], {}
    for entry in (sheet.get("registry") or {}).get("wasms") or []:
        family, version = entry["family"], str(entry["version"])
        key = f"{family}@{version}"
        rows.append({"key": key, "path": store_key(WASM_NAMESPACE, registry_path(family, version)),
                     "sha256": sha.get((family, version)) or entry.get("sha256", ""),
                     "type": entry.get("wasm_type", ""), "memory_keep": "true" if entry.get("memory_keep") else ""})
        keys.setdefault(family, key)
        keys[key] = key
    return rows, keys


def conductor_section(sheet: dict, ids: dict, keys: dict, store_id: str) -> dict:
    stands = [{"name": "conductor", "canisters": [
        {"name": n, "kind": "frontend" if k != "backend" else "backend", "canister_id": ids[n], "adopted": True,
         "status": "ready"} for k, n in CONDUCTOR_NAMES.items() if ids.get(n)]}]
    ms = (sheet.get("governance") or {}).get("multisig")
    if isinstance(ms, dict):
        signers = "; ".join(f'principal "{s}"' for s in dict.fromkeys(ms.get("signers") or []))
        config = [{"method": "configure", "arg": f"(vec {{ {signers} }} : vec principal, "
                   f"{int(ms.get('threshold') or 1)} : nat, {int(ms.get('expiry_secs') or 604800)} : nat)"}]
        stands.append({"name": "governance", "canisters": [
            {"name": MULTISIG_NAME, "kind": "backend", "wasm": keys.get(ms["wasm"], ms["wasm"]),
             "controllers": ms.get("controllers") or ["$this"], "config": config}]})
    return {"name": "Casals", "stands": stands}


def normalize(sheet: dict, env: str, *, deployer: str, ids: dict, uploads: list[dict]) -> dict:
    """``ids`` maps conductor names (and adopted canisters) to canister ids."""
    sheet = strip_comments(sheet)
    resolved, _ = resolve_partial(sheet, env, ResolveContext(deployer=deployer, self_id=ids.get("casals-backend")))
    resolved = json.loads(json.dumps(resolved).replace("$multisig", f"$canister:{MULTISIG_NAME}"))
    store_id = ids.get(CONDUCTOR_NAMES["store"], "")
    wasms, keys = wasm_rows(resolved, uploads)
    adopted = {**(env_block(resolved, env).get("bindings") or {}), **ids}
    cycles = {**(resolved.get("cycles") or {}), **(env_block(resolved, env).get("cycles") or {})}
    sections = [conductor_section(resolved, ids, keys, store_id)]
    sections += [section(s, keys, store_id, adopted) for s in resolved.get("sections") or []]
    doc_grants = grants("orchestra", (resolved.get("conductor") or {}).get("commanders"))
    for sec in resolved.get("sections") or []:
        doc_grants += grants(f"section:{sec['name']}", sec.get("commanders"))
        creator = (section_arrangement(sec) or {}).get("created_by")
        if creator:
            doc_grants += grants(f"section:{sec['name']}", [{"principal": creator, "permissions": "stand.create"}])
        for st in sec.get("stands") or []:
            doc_grants += grants(f"stand:{st['name']}", st.get("commanders"))
    min_cycles = int(float(cycles.get("min_balance_tc") or 0) * TC)
    return {
        "name": resolved.get("name", ""), "description": resolved.get("description", ""), "env": env,
        "network": env_block(resolved, env).get("network", ""), "vars": {"$deployer": deployer},
        "store_id": store_id, "frontend_id": ids.get(CONDUCTOR_NAMES["frontend"], ""),
        "multisig_id": f"$canister:{MULTISIG_NAME}" if (resolved.get("governance") or {}).get("multisig") else "",
        "reserve": int(float(cycles.get("conductor_min_balance_tc") or 0) * TC),
        "policy": {"min": min_cycles, "topup": min_cycles}, "monitor": env_monitor(resolved, env) or {},
        "snapshots_keep": int((resolved.get("snapshots") or {}).get("keep") or 2),
        "wasms": wasms, "sections": sections, "grants": doc_grants,
    }
