"""casals show and casals graph — live orchestra views."""

from __future__ import annotations

from typing import Any

from sheetv2 import (
    ResolveContext,
    env_block,
    iter_canisters,
    resolve_partial,
)

from casals_cli.bindings import live_bindings
from casals_cli.util import cycles_to_tc, emit_json


def _name_for_principal(principal: str, id_to_name: dict[str, str]) -> str:
    return id_to_name.get(principal, principal[:12] + "…")


def build_live_view(ic, sheet: dict, env: str, backend_id: str, bindings: dict[str, str]) -> dict[str, Any]:
    """Assemble show payload from conductor queries + IC read_state."""
    tree = ic.query(backend_id, "get_tree")
    sheet_res = ic.query(backend_id, "get_sheet")
    plan_res = ic.call_update(backend_id, "plan", "{}")
    orch = ic.query(backend_id, "orchestration_status", "{}")
    last_apply = ic.query(backend_id, "last_apply")

    id_to_name = {v: k for k, v in bindings.items()}
    if isinstance(tree, dict):
        for alias, name in (tree.get("principal_aliases") or {}).items():
            id_to_name.setdefault(alias, name)

    ctx = ResolveContext(
        deployer=ic.deployer_principal(),
        self_id=backend_id,
        canister_ids=dict(bindings),
        env_values=env_block(sheet, env),
    )
    resolved, _ = resolve_partial(sheet, env, ctx, partial=True)

    canisters_out = []
    for section, stand, cname, canister in iter_canisters(resolved):
        cid = bindings.get(cname, "")
        row: dict[str, Any] = {
            "section": section.get("name"),
            "stand": stand.get("name"),
            "name": cname,
            "kind": canister.get("kind"),
            "mode": canister.get("mode", "managed"),
            "wasm": canister.get("wasm"),
            "canister_id": cid or None,
        }
        if cid:
            try:
                ctrls = ic.read_controllers(cid)
                row["ic_controllers"] = ctrls
                row["ic_controllers_named"] = [_name_for_principal(p, id_to_name) for p in ctrls]
            except Exception as exc:
                row["ic_controllers_error"] = str(exc)
            mh = ic.read_module_hash(cid)
            if mh:
                row["module_hash"] = mh
            cyc = ic.canister_cycles(cid)
            row["cycles_tc"] = cycles_to_tc(cyc) if cyc is not None else "n/a"
        canisters_out.append(row)

    plan = (plan_res.get("plan") or {}) if isinstance(plan_res, dict) else {}
    return {
        "ok": True,
        "env": env,
        "backend_id": backend_id,
        "sheet": sheet_res,
        "bindings": bindings,
        "tree": tree,
        "orchestration_status": orch,
        "last_apply": last_apply,
        "canisters": canisters_out,
        "plan_summary": {
            "hash": plan.get("hash"),
            "items": len(plan.get("items") or []),
            "unmanaged": len(plan.get("unmanaged") or []),
            "drift": len(plan.get("drift") or []),
        },
    }


def render_show_text(view: dict) -> str:
    lines = [
        f"env={view.get('env')} backend={view.get('backend_id')}",
        f"plan: {view.get('plan_summary', {}).get('items', 0)} items, "
        f"{view.get('plan_summary', {}).get('unmanaged', 0)} unmanaged",
        "",
        f"{'SECTION':<12} {'STAND':<14} {'CANISTER':<22} {'ID':<14} {'MODE':<8} {'CYCLES':>8}",
        "-" * 90,
    ]
    for row in view.get("canisters") or []:
        cid = (row.get("canister_id") or "")[:13]
        cyc = row.get("cycles_tc")
        cyc_s = f"{cyc:.2f}" if isinstance(cyc, (int, float)) else str(cyc)
        lines.append(
            f"{(row.get('section') or ''):<12} {(row.get('stand') or ''):<14} "
            f"{(row.get('name') or ''):<22} {cid:<14} {(row.get('mode') or ''):<8} {cyc_s:>8}"
        )
        ctrls = row.get("ic_controllers_named") or row.get("ic_controllers") or []
        if ctrls:
            lines.append(f"             IC controllers: {', '.join(ctrls)}")
    return "\n".join(lines)


def mermaid_graph(view: dict, sheet: dict, bindings: dict[str, str]) -> str:
    """Emit Mermaid flowchart with three edge types per orchestra-control-graph-spec."""
    lines = ["flowchart LR"]
    id_to_name = {v: k for k, v in bindings.items()}
    tree = view.get("tree") or {}
    node_ids: dict[str, str] = {}

    def nid(label: str) -> str:
        safe = label.replace("-", "_").replace(".", "_")
        if safe not in node_ids:
            node_ids[safe] = safe
        return safe

    for row in view.get("canisters") or []:
        cname = row.get("name") or ""
        cid = row.get("canister_id") or ""
        if not cid:
            continue
        lines.append(f'  {nid(cname)}["{cname}"]')
        for p in row.get("ic_controllers") or []:
            plabel = _name_for_principal(p, id_to_name)
            lines.append(f'  {nid(plabel)}["{plabel}"]')
            lines.append(f"  {nid(plabel)} -->|ic_controller| {nid(cname)}")

    if isinstance(tree, dict):
        for sec in tree.get("sections") or []:
            for stand in sec.get("stands") or []:
                st_name = stand.get("name") or ""
                for cmd in stand.get("commanders") or []:
                    pr = cmd.get("principal") or ""
                    plabel = _name_for_principal(pr, id_to_name)
                    lines.append(f'  {nid(plabel)}["{plabel}"]')
                    lines.append(f"  {nid(plabel)} -.->|casals_commander| {nid(st_name)}")

    orch = view.get("orchestration_status") or {}
    for baton in orch.get("batons") or []:
        bid = baton.get("canister_id") or ""
        bname = baton.get("name") or bid[:12]
        lines.append(f'  {nid(bname)}["{bname} baton"]')
        top = (baton.get("config") or {}).get("top_commander") or ""
        if top:
            tlabel = _name_for_principal(top, id_to_name)
            lines.append(f"  {nid(tlabel)} -.->|baton| {nid(bname)}")
        for mid in baton.get("managed_canisters") or []:
            mlabel = _name_for_principal(mid, id_to_name) if mid in id_to_name else mid[:12]
            lines.append(f"  {nid(bname)} -.->|baton_manages| {nid(mlabel)}")

    return "\n".join(lines)


def cmd_show(ic, args, sheet: dict) -> None:
    backend, bmap = live_bindings(ic, str(sheet.get("name") or ""), args.env, getattr(args, "conductor", None))
    view = build_live_view(ic, sheet, args.env, backend, bmap)
    if getattr(args, "json", False):
        emit_json(view)
    else:
        print(render_show_text(view))


def cmd_graph(ic, args, sheet: dict) -> None:
    backend, bmap = live_bindings(ic, str(sheet.get("name") or ""), args.env, getattr(args, "conductor", None))
    view = build_live_view(ic, sheet, args.env, backend, bmap)
    if getattr(args, "ascii", False):
        print(render_show_text(view))
    else:
        print(mermaid_graph(view, sheet, bmap))
