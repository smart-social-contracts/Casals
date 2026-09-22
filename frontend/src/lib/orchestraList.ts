/**
 * Orchestra *List* view — the flat, selectable table of canisters.
 *
 * Pure helpers: flattening the tree into rows, deciding which toolbar action
 * is available for the current selection (permissions × state), and remembering
 * the chosen view. The Svelte page only wires these to the DOM.
 *
 * No runtime imports (type-only), so `node --test` runs the `.test.ts` next to
 * it without a bundler: the permission ladder and the core-section rule are
 * passed in by the caller.
 */

import type { Canister, IcRunStatus, Section, Stand, Tree } from './api';

export type OrchestraView = 'list' | 'diagram' | 'control';

const VIEW_KEY = 'casals.orchestra.view';
const VIEWS: readonly OrchestraView[] = ['list', 'diagram', 'control'];

export function readOrchestraView(storage: Pick<Storage, 'getItem'> | null | undefined): OrchestraView {
  const raw = storage?.getItem(VIEW_KEY) ?? '';
  return (VIEWS as readonly string[]).includes(raw) ? (raw as OrchestraView) : 'list';
}

export function writeOrchestraView(storage: Pick<Storage, 'setItem'> | null | undefined, view: OrchestraView): void {
  try {
    storage?.setItem(VIEW_KEY, view);
  } catch {
    /* private mode / quota — the choice just does not persist */
  }
}

export interface ListRow {
  /** Stable key for the table row. */
  key: string;
  section: Section;
  stand: Stand;
  canister: Canister;
  /** Casals core (conductor / governance) — named and placed by the sheet: no rename, no delete. */
  core: boolean;
}

/** Every canister of the tree as one row, in tree order (sections → stands → `sort`). */
export function flattenTreeRows(
  tree: Tree | null | undefined,
  opts: {
    /** Order canisters within a stand (default: as given). */
    sort?: (canisters: Canister[]) => Canister[];
    /** Which section is the synthetic Casals one (`isOrchestraSectionName`). */
    isCore?: (sectionName: string) => boolean;
  } = {},
): ListRow[] {
  const sort = opts.sort ?? ((c: Canister[]) => c);
  const isCore = opts.isCore ?? (() => false);
  const rows: ListRow[] = [];
  if (!tree) return rows;
  tree.sections.forEach((section, si) => {
    const core = isCore(section.name);
    section.stands.forEach((stand, di) => {
      sort(stand.canisters).forEach((canister, ci) => {
        rows.push({
          key: `${si}|${section.name}/${di}|${stand.name}/${canister.canister_id || `${canister.name}#${ci}`}`,
          section,
          stand,
          canister,
          core,
        });
      });
    });
  });
  return rows;
}

/** Rows that can be selected: only a provisioned canister can be acted upon. */
export function selectableRows(rows: ListRow[]): ListRow[] {
  return rows.filter((r) => !!r.canister.canister_id);
}

export type ListAction =
  | 'deploy'
  | 'bundle'
  | 'snapshot'
  | 'revert'
  | 'stop'
  | 'start'
  | 'rename'
  | 'tags'
  | 'delete';

/** Backend permission key each toolbar action is guarded by (`auth.PERMISSIONS`). */
export const ACTION_PERMISSION: Record<ListAction, string> = {
  deploy: 'canister.deploy',
  bundle: 'canister.deploy',
  snapshot: 'canister.snapshot',
  revert: 'canister.revert',
  stop: 'canister.lifecycle',
  start: 'canister.lifecycle',
  rename: 'canister.rename',
  tags: 'canister.tag',
  delete: 'canister.delete',
};

export interface ActionContext {
  /** IC controllers of the conductor bypass commander checks (mirrors `_require_commander`). */
  isController: boolean;
  /** The commander ladder: may the session use `permissionKey` on this row's stand? */
  allows: (row: ListRow, permissionKey: string) => boolean;
  /** Cached IC run status, when known. */
  runtimeOf: (canisterId: string) => IcRunStatus | undefined;
}

/** A canister that serves assets — the only kind a bundle can be deployed to.
 * The tree's `kind` is not reliable for that (frontends registered as
 * `backend` exist), so the wasm type / tags decide, with `kind` as fallback. */
export function isFrontend(c: Pick<Canister, 'kind' | 'wasm_type' | 'tags' | 'wasm_key'>): boolean {
  const t = (c.wasm_type ?? '').toLowerCase();
  if (t) return t === 'assets' || t === 'frontend' || t === 'certified-assets';
  const tags = (c.tags ?? []).map((x) => x.toLowerCase());
  if (tags.includes('assets') || tags.includes('frontend')) return true;
  const k = (c.wasm_key ?? '').toLowerCase();
  if (k.includes('assets') || k.includes('frontend')) return true;
  return c.kind === 'frontend';
}

/** True when the session may perform `action` on this row. */
export function canOnRow(row: ListRow, action: ListAction, ctx: ActionContext): boolean {
  return ctx.isController || ctx.allows(row, ACTION_PERMISSION[action]);
}

/**
 * Which toolbar buttons are enabled for `selected`.
 *
 * - Nothing selected → nothing enabled.
 * - Deploy, Deploy frontend bundle, Rename, Tags act on exactly one canister
 *   (the modals are per-canister); the bundle one only on a frontend.
 * - Rename and Delete never touch Casals core canisters.
 * - Revert needs a snapshot on every selected canister.
 * - Stop is pointless when every selected canister is known to be stopped;
 *   Start when every one is known to be running. Unknown status keeps both on.
 * - Every action needs the permission on every selected row.
 */
export function listActionState(selected: ListRow[], ctx: ActionContext): Record<ListAction, boolean> {
  const off: Record<ListAction, boolean> = {
    deploy: false, bundle: false, snapshot: false, revert: false, stop: false,
    start: false, rename: false, tags: false, delete: false,
  };
  if (!selected.length) return off;
  const all = (action: ListAction) => selected.every((r) => canOnRow(r, action, ctx));
  const one = selected.length === 1;
  const anyCore = selected.some((r) => r.core);
  const statuses = selected.map((r) => ctx.runtimeOf(r.canister.canister_id));
  return {
    deploy: one && all('deploy'),
    bundle: one && isFrontend(selected[0].canister) && all('bundle'),
    snapshot: all('snapshot'),
    revert: all('revert') && selected.every((r) => !!r.canister.snapshot_id),
    stop: all('stop') && !statuses.every((s) => s === 'stopped'),
    start: all('start') && !statuses.every((s) => s === 'running'),
    rename: one && !anyCore && all('rename'),
    tags: one && all('tags'),
    delete: !anyCore && all('delete'),
  };
}
