# Orchestra control graph — Diagram view spec

> **Status:** Implemented
> **Issue:** [Casals#42](https://github.com/smart-social-contracts/Casals/issues/42)
> **App:** `frontend/src/lib/components/OrchestraDiagram.svelte`, `frontend/src/lib/orchestraGovernance.ts`
> **Repo:** smart-social-contracts/Casals

The Orchestra **Diagram** view today shows **sheet topology** (Section → Stand → Canister). Operators also need a **control graph** that shows who can change or destroy each canister — without conflating IC **controllers**, Casals **commanders**, and Baton **upgrade policy**.

Related: [Casals#41](https://github.com/smart-social-contracts/Casals/issues/41) (governance layer naming / navigation). This issue is the Orchestra-specific visualization complement.

---

## Problem

Diagram view (`orchestraView === 'diagram'`) renders an organizational chart:

```
Orchestra → Section → Stand → Canister
```

Node colors encode **canister role** (casals, multisig, baton, backend, frontend). Connecting lines encode **membership in the sheet**, not authority.

That answers: *what did we deploy?*

It does **not** answer:

- Who is an **IC controller** of this canister?
- Who is the **Casals commander** for this section/stand?
- Which **baton** governs managed upgrades, and is it also an IC co-controller?
- What is the break-glass path (multisig → Casals → baton → realm canisters)?

Some hints exist today (`managed · {baton}` + optional `IC ctrl` on chips in `OrchestraDiagram.svelte`), but there are **no edges** and no distinction between controller vs commander vs baton policy.

Realms/GaaS operators routinely debug incidents with questions like “can the realm backend self-upgrade?” vs “can multisig wipe this canister?” — the current diagram cannot answer those at a glance.

---

## Goals

1. Add a **Control graph** lens to Orchestra (alongside Tree and the existing org Diagram — see [View modes](#view-modes)).
2. Draw **typed edges** for three distinct relationships (see [Edge types](#edge-types)).
3. Make the graph **per conductor instance** (one Casals orchestra = one graph).
4. Reuse data already on `get_tree` and `orchestration_status` where possible; live IC controller fetch only when cache is stale or missing.
5. Complement #41: when the governance map exists, link from Control graph ↔ governance screens.

## Non-goals

- Replacing Tree view or removing the org-topology Diagram.
- Collapsing IC controllers and Casals commanders into one undifferentiated “controls” edge.
- Showing arrangement `execute_principals`, Casals action-approval policies, or multisig pending proposals on this graph (those belong on governance pages per #41).
- Backend permission key renames or protocol changes to multisig/Baton.

---

## Control planes (conceptual model)

Casals orchestras expose **three** authority layers relevant to this graph:

| Layer | Meaning | Example |
|---|---|---|
| **IC controller** | Principal(s) on the IC management canister `controllers` list — can `install_code`, `update_settings`, stop/start, etc. | multisig, casals-backend, baton (after hand-off), CycleOps, deployer |
| **Casals commander** | Principal delegated Casals permissions on a **section** or **stand** via `set_commander` | installer on `Deployments` section; realm backend as stand commander |
| **Baton upgrade policy** | Baton `top_commander`, baton `commanders`, approval threshold for managed upgrades | casals-backend + realm-backend, 2-of-2 on `dominion-baton` |

**Invariant:** commander ≠ IC controller. A realm backend may be stand commander without being the only IC controller; a baton may be IC co-controller without being the Casals stand commander.

### Reference topology (Realms/GaaS)

Documented in Realms `casals-config/sheets/realms.json` and GaaS seed runbooks:

```text
[multisig] ──IC ctrl──► casals-backend, every baton, …
[casals-backend] ──IC ctrl / provisions──► orchestra canisters
[casals-backend] ──top_commander──► [realm-baton]
[realm-baton] ──IC co-ctrl (after hand_to_baton)──► realm backend + frontend
[realm-baton] ──commanders (2-of-2 policy)──► casals-backend + realm-backend
[installer] ──section commander──► Deployments (stand.create, …)
[realm-backend] ──stand commander──► its stand (casals.upgrade_to path)
```

The graph is a **DAG**, not a single-root tree. Layout should tolerate multiple parents (e.g. a canister with multisig + Casals + baton + CycleOps as IC controllers).

---

## View modes

| Mode | Purpose | Layout |
|---|---|---|
| **Tree** (existing) | Navigate, edit, rollout | Collapsible section/stand list |
| **Diagram** (existing) | Sheet inventory at a glance | Section columns, stand cards, canister chips |
| **Control graph** (new) | Authority / break-glass debugging | Force-directed or layered DAG; nodes = principals + canisters |

**UI proposal:** extend the Tree | Diagram toggle to **Tree | Diagram | Control** (or Diagram sub-toggle: **Topology | Control** if a third top-level tab feels heavy).

Persist selection in URL query (`?orchestraView=control`) like existing `diagram`/`tree`.

---

## Edge types

Use visually distinct edges. Suggested defaults:

| Type | Style | From → To | Data source |
|---|---|---|---|
| `ic_controller` | Solid arrow | Controller principal → target canister | `canister.controllers[]` on `get_tree`; optional live `canister_status` refresh |
| `casals_commander` | Dashed arrow | Commander principal → section, stand, or canister | `section.commanders`, `stand.commanders` on `get_tree` |
| `baton_top_commander` | Dotted arrow | top_commander (usually casals-backend) → baton canister | `orchestration_status.batons[].config` or baton query |
| `baton_commander` | Dotted bidirectional or labeled | Baton commanders ↔ baton | `orchestration_status.batons[].commanders` |
| `baton_manages` | Orange dashed | Baton → managed canister | `orchestration_status.batons[].managed_canisters` (already used by `managedByBaton`) |
| `baton_ic_control` | Solid + badge | Baton → canister when baton is also IC controller | `batonControlsTarget()` (existing helper) |

**Legend** must list every enabled edge type. Toggle chips: **IC controllers | Commanders | Baton** (default: all on).

Hovering an edge shows a plain-language tooltip, e.g.:

- *IC controller — can call install_code / update_settings*
- *Stand commander — can invoke casals.upgrade_to for this stand*
- *Baton managed — upgrades require baton approval policy*

---

## Nodes

### Canister nodes

Reuse existing chip styling from `OrchestraDiagram.svelte` / `CanisterTypeBadges`. Show:

- name, kind, wasm_type badges
- canister id (truncated)
- optional status chip: `N controllers`, `managed`, `stand commander: …`

### Principal nodes

For controllers/commanders that are not themselves orchestra canisters (rare) or when grouping:

- Use `controllerLabel()` from `controllerLabels.ts` for display names (CycleOps, deployer, etc.).
- Principals that match a canister in the tree should **prefer the canister node** (one node, multiple edge types) to avoid duplication.

### Special nodes

- **Orchestra** root node optional in Control graph (or omit — graph starts at governance apex).
- **multisig** and **casals-backend** should be visually prominent when IC-controller edges are shown.

---

## Data sources

### Already available (no backend change required for MVP)

| API | Fields used today |
|---|---|
| `get_tree` | `section.commanders`, `stand.commanders`, `canister.controllers` (`views._canister_view`) |
| `orchestration_status` | `batons[]` with `managed_canisters`, `commanders`, `casals_is_commander`, `config` |

Frontend already loads both on Orchestra home (`+page.svelte`: `tree`, `orchStatus`).

### Optional enhancements (follow-up issues)

| Enhancement | Why |
|---|---|
| `get_tree` includes `stand.commander_principal` edges to canisters in stand | Explicit stand→member commander visualization without inferring |
| Batch `canister_status` for stale `ic_controllers` cache | Accurate after out-of-band controller changes |
| `orchestration_status` returns `top_commander` per baton | Avoid per-baton query from UI |
| Principal alias registry in Casals settings | Friendlier labels for GaaS deployer / II principals |

MVP may show a **stale cache warning** when `controllers` is empty but baton reports `baton_ic_control`.

---

## Layout & interaction

- **Layout:** layered DAG top-to-bottom with suggested gravity:
  1. multisig (when IC edges on)
  2. casals-backend
  3. batons + section commanders
  4. managed / stand canisters
- Support **pan + zoom** (org Diagram already scrolls horizontally; Control graph needs 2D pan).
- **Click** canister → existing canister detail / governance console (`governanceConsolePath`).
- **Click** principal → Operator access page filtered to that principal (when #41 lands) or copy principal.
- **Filter** respects existing Orchestra search (`filterQuery`) — hide nodes with no matching canister/principal.
- **Export** (nice-to-have): PNG or “copy Mermaid” for runbooks.

---

## Implementation sketch

### New / extended files

```
frontend/src/lib/components/OrchestraControlGraph.svelte   # graph canvas
frontend/src/lib/orchestraControlGraph.ts                  # build nodes/edges from tree + orch status
frontend/src/lib/components/OrchestraDiagram.svelte          # unchanged (topology)
frontend/src/routes/+page.svelte                             # third view mode + toggle
```

### `buildControlGraph(tree, orchestrationStatus, options)`

Returns `{ nodes: ControlNode[], edges: ControlEdge[] }`.

Reuse from `orchestraGovernance.ts`:

- `resolveBatons`, `managedByBaton`, `canisterGovernanceMeta`, `batonControlsTarget`
- `controllerEntries`, `controllerLabel`

### Rendering library

Pick one consistent with Casals frontend bundle size:

- Lightweight: custom SVG + simple force simulation, or
- Existing dep if already in package.json (check before adding `d3` / `@xyflow/svelte`).

---

## Acceptance criteria

- [ ] Operator can switch to **Control graph** from Orchestra without losing Tree/Diagram behavior.
- [ ] Graph shows **distinct edge styles** for IC controller vs Casals commander vs baton relationships.
- [ ] Toggling edge layers updates the graph without a full page reload.
- [ ] For a typical GaaS sheet realm (baton + backend + frontend), user can see: multisig/casals/baton IC edges, baton→managed edges, and stand commander on the realm backend stand.
- [ ] Hover/tooltip explains each edge type in plain language (no raw permission keys).
- [ ] Empty/partial data degrades gracefully (missing baton config, empty controllers cache) with inline warning, not a broken graph.
- [ ] Works for orchestras with **multiple batons** (`orchestration_status.batons[]`).
- [ ] No regression to existing Diagram legend and section layout.

---

## Test plan

### Manual (GaaS / Realms test conductor)

1. Open Orchestra → Control graph on a seeded GaaS instance (`testrealm7` stand).
2. Confirm multisig → casals-backend → baton → realm canisters IC chain is visible when IC layer enabled.
3. Confirm `installer` appears as section commander on `Deployments` when commander layer enabled.
4. Toggle layers off one at a time; edges disappear, nodes without edges may dim or hide per product choice.
5. Compare one canister’s controllers against `dfx canister info <id>` (or `icp canister status`) — should match when cache fresh.

### Automated

- Unit tests for `buildControlGraph()`:
  - multisig + casals + baton + managed canister fixture
  - multiple IC controllers on one canister → multiple `ic_controller` edges
  - commander on section only (no stand commander)
  - missing `orchestration_status` → baton edges omitted, warning flag set

---

## Open questions

1. **Sub-mode vs top-level tab:** `Diagram: Topology | Control` vs third `Control` tab alongside Tree/Diagram?
2. **Live controller refresh:** on-by-default for Control graph load, or button “Refresh IC controllers”?
3. **Show CycleOps / extra_controller_principals** with distinct styling vs generic principal blob?
4. **Quarters:** when capital provisions quarter backends, inherited controllers — show inferred edge from capital backend or only cached `controllers`?

---

## References

- Realms sheet topology + baton comments: `realms/casals-config/sheets/realms.json`
- Realms operator docs: `realms/AGENTS.md` (Quarter scaling and controllers)
- Casals `get_tree` serialization: `src/views.py` (`controllers`, `commanders`)
- Casals orchestration snapshot: `src/orchestration_bridge.py` (`_orchestration_status_all_gen`)
- Existing diagram: `frontend/src/lib/components/OrchestraDiagram.svelte`
- Governance UX umbrella: [Casals#41](https://github.com/smart-social-contracts/Casals/issues/41)
