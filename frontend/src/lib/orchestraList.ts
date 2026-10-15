/**
 * Orchestra *List* view: flat selectable rows, toolbar availability, and the
 * remembered layout. Type-only imports so `node --test` runs without a bundler.
 */

import type { CanisterRow, OrchestraView, SectionView, StandView } from './api';

export type OrchestraLayout = 'list' | 'diagram' | 'control';

const VIEW_KEY = 'casals.orchestra.view';
const VIEWS: readonly OrchestraLayout[] = ['list', 'diagram', 'control'];

export function readOrchestraView(storage: Pick<Storage, 'getItem'> | null | undefined): OrchestraLayout {
  const raw = storage?.getItem(VIEW_KEY) ?? '';
  return (VIEWS as readonly string[]).includes(raw) ? (raw as OrchestraLayout) : 'list';
}

export function writeOrchestraView(storage: Pick<Storage, 'setItem'> | null | undefined, view: OrchestraLayout): void {
  try {
    storage?.setItem(VIEW_KEY, view);
  } catch {
    /* private mode / quota — the choice just does not persist */
  }
}

export interface ListRow {
  /** Stable key for the table row. */
  key: string;
  section: SectionView;
  stand: StandView;
  canister: CanisterRow;
  /** Casals core (conductor / governance) — named and placed by the sheet: no rename, no delete. */
  core: boolean;
}

/** Every canister of the view as one row, in view order (sections → stands → `sort`). */
export function flattenTreeRows(
  view: OrchestraView | null | undefined,
  opts: {
    /** Order canisters within a stand (default: as given). */
    sort?: (canisters: CanisterRow[]) => CanisterRow[];
    /** Which section is the Casals one (`isOrchestraSectionName`). */
    isCore?: (sectionName: string) => boolean;
  } = {},
): ListRow[] {
  const sort = opts.sort ?? ((c: CanisterRow[]) => c);
  const isCore = opts.isCore ?? (() => false);
  const rows: ListRow[] = [];
  if (!view) return rows;
  view.sections.forEach((section, si) => {
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

/** Conductor permission key each toolbar action is guarded by (`auth.KEYS`). */
export const ACTION_PERMISSION: Record<ListAction, string> = {
  deploy: 'canister.deploy',
  bundle: 'canister.deploy',
  snapshot: 'canister.lifecycle',
  revert: 'canister.lifecycle',
  stop: 'canister.lifecycle',
  start: 'canister.lifecycle',
  rename: 'canister.config',
  tags: 'canister.config',
  delete: 'canister.delete',
};

export interface ActionContext {
  /** IC controllers of the conductor bypass grant checks. */
  isController: boolean;
  /** May the session use `permissionKey` on this row's stand? */
  allows: (row: ListRow, permissionKey: string) => boolean;
}

/** A canister that serves assets — the only kind a bundle can be deployed to
 * (frontends registered as `backend` exist, so tags and wasm also count). */
export function isFrontend(c: Pick<CanisterRow, 'kind' | 'tags' | 'wasm'>): boolean {
  return c.kind === 'frontend' || /assets|frontend/i.test(c.wasm) || c.tags.some((t) => /^(assets|frontend)$/i.test(t));
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
 * - Stop needs a selected canister that is not stopped; Start one that is.
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
  const stopped = selected.map((r) => r.canister.status === 'stopped');
  return {
    deploy: one && all('deploy'),
    bundle: one && isFrontend(selected[0].canister) && all('bundle'),
    snapshot: all('snapshot'),
    revert: all('revert') && selected.every((r) => !!r.canister.snapshot),
    stop: all('stop') && stopped.includes(false),
    start: all('start') && stopped.includes(true),
    rename: one && !anyCore && all('rename'),
    tags: one && all('tags'),
    delete: !anyCore && all('delete'),
  };
}
