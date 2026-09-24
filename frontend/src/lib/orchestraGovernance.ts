/** Orchestra governance helpers — multisig, batons, controllers, managed-canister links. */

import type { Canister, Tree, Section, Stand } from './api';
import {
  isBatonWasm,
  isMultisigWasm,
  batonControlsTarget,
  batonConsoleUrl,
  multisigConsoleUrl,
} from './orchestrationNav';
import { coalesceControllers } from './controllerTree';
import { controllerLabel } from './controllerLabels';
import { isOrchestraSectionName } from './governanceUx';

export { coalesceControllers };

export interface LocatedCanister extends Canister {
  section: string;
  stand: string;
}

export interface BatonRef {
  name: string;
  canister_id: string;
  section?: string;
  stand?: string;
}

export interface CanisterGovernanceMeta {
  isBaton: boolean;
  isMultisig: boolean;
  managedBy: BatonRef | null;
  batonIsController: boolean;
}

export function isMultisigCanister(c: Pick<Canister, 'wasm_key' | 'name'>): boolean {
  return isMultisigWasm(c.wasm_key) || c.name === 'multisig';
}

export function isBatonCanister(c: Pick<Canister, 'wasm_key'>): boolean {
  return isBatonWasm(c.wasm_key);
}

export function findMultisigCanister(tree: Tree | null | undefined): LocatedCanister | null {
  if (!tree) return null;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (isMultisigCanister(c)) {
          return { ...c, section: sec.name, stand: stand.name };
        }
      }
    }
  }
  return null;
}

export function findBatonsInTree(tree: Tree | null | undefined): LocatedCanister[] {
  if (!tree) return [];
  const out: LocatedCanister[] = [];
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (isBatonCanister(c)) {
          out.push({ ...c, section: sec.name, stand: stand.name });
        }
      }
    }
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

export function batonForStand(tree: Tree | null | undefined, standName: string): LocatedCanister | null {
  if (!tree || !standName) return null;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      if (stand.name !== standName) continue;
      for (const c of stand.canisters) {
        if (isBatonCanister(c)) {
          return { ...c, section: sec.name, stand: stand.name };
        }
      }
    }
  }
  return null;
}

export function managedByBaton(
  canister: Canister,
  batons: BatonRef[],
  tree: Tree | null | undefined,
): BatonRef | null {
  if (!canister.canister_id || isBatonCanister(canister) || isMultisigCanister(canister)) {
    return null;
  }
  for (const b of batons) {
    if (batonControlsTarget(tree, b.canister_id, canister.canister_id)) return b;
  }
  return null;
}

export function canisterGovernanceMeta(
  canister: Canister,
  batons: BatonRef[],
  tree: Tree | null | undefined,
): CanisterGovernanceMeta {
  const isBaton = isBatonCanister(canister);
  const isMultisig = isMultisigCanister(canister);
  const managedBy = managedByBaton(canister, batons, tree);
  const batonIsController = managedBy
    ? batonControlsTarget(tree, managedBy.canister_id, canister.canister_id)
    : false;
  return { isBaton, isMultisig, managedBy, batonIsController };
}

/** Governance canisters first: multisig, batons, then everything else. */
export function sortCanistersForDisplay(canisters: Canister[]): Canister[] {
  const rank = (c: Canister) => {
    if (isMultisigCanister(c)) return 0;
    if (isBatonCanister(c)) return 1;
    return 2;
  };
  return [...canisters].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

export function governanceConsolePath(canister: Canister): string | null {
  if (isBatonCanister(canister) && canister.canister_id) return batonConsoleUrl(canister.canister_id);
  if (isMultisigCanister(canister) && canister.canister_id) return multisigConsoleUrl(canister.canister_id);
  return null;
}

export function controllerEntries(
  controllers: string[] | undefined,
  labels: Map<string, string>,
): Array<{ principal: string; display: string; title: string }> {
  return (controllers ?? []).map((principal) => {
    const label = controllerLabel(principal, labels);
    return { principal, display: label.display, title: label.title };
  });
}

export function countGovernanceCanisters(tree: Tree | null | undefined): {
  multisig: number;
  batons: number;
  total: number;
} {
  let multisig = 0;
  let batons = 0;
  let total = 0;
  if (!tree) return { multisig, batons, total };
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        total += 1;
        if (isMultisigCanister(c)) multisig += 1;
        else if (isBatonCanister(c)) batons += 1;
      }
    }
  }
  return { multisig, batons, total };
}

// ── Casals core section ──────────────────────────────────────────────────────
//
// The backend homes its own canisters on one synthetic section, `Casals`, with
// two stands: `conductor` (casals-backend, casals-frontend, casals-store)
// and `governance` (multisig). `augmentTreeWithCasals`
// only hoists that section to the top and merges live controllers into it.
// The synthesis below is a fallback for cached trees from older backends that
// did not put the conductor canisters on a stand.

export const CASALS_CONDUCTOR_STAND = 'conductor';
export const CASALS_GOVERNANCE_STAND = 'governance';

const CASALS_CANISTER_NAMES = new Set([
  'casals-backend',
  'casals-frontend',
  'casals-store',
]);

export function isCasalsCanister(c: Pick<Canister, 'name'>): boolean {
  return CASALS_CANISTER_NAMES.has(c.name);
}

/** @deprecated Use isCasalsCanister */
export const isConductorCanister = isCasalsCanister;

export function treeContainsCanisterId(tree: Tree | null | undefined, canisterId: string): boolean {
  if (!tree || !canisterId) return false;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id === canisterId) return true;
      }
    }
  }
  return false;
}

function isCasalsSectionName(name: string): boolean {
  return isOrchestraSectionName(name);
}

function canisterMatches(a: Canister, b: Canister): boolean {
  if (a.canister_id && b.canister_id && a.canister_id === b.canister_id) return true;
  if (a.name && b.name && a.name === b.name) return true;
  return false;
}

type CoreRole = 'backend' | 'frontend' | 'multisig' | null;

function coreRole(c: Canister, backendId: string, frontendId: string): CoreRole {
  if ((backendId && c.canister_id === backendId) || c.name === 'casals-backend') return 'backend';
  if ((frontendId && c.canister_id === frontendId) || c.name === 'casals-frontend') return 'frontend';
  if (isMultisigCanister(c)) return 'multisig';
  return null;
}

function syntheticCasalsBackend(backendId: string, controllers?: string[]): Canister {
  return {
    name: 'casals-backend',
    canister_id: backendId,
    kind: 'backend',
    wasm_key: 'casals-backend',
    wasm_type: 'basilisk',
    wasm_hash: '',
    status: 'installed',
    url: '',
    snapshot_id: '',
    controllers,
  };
}

function syntheticCasalsFrontend(frontendId: string, controllers?: string[]): Canister {
  return {
    name: 'casals-frontend',
    canister_id: frontendId,
    kind: 'frontend',
    wasm_key: 'casals-frontend',
    wasm_type: 'assets',
    wasm_hash: '',
    status: 'installed',
    url: '',
    snapshot_id: '',
    controllers,
  };
}

function withStandCanisters(section: Section, standName: string, description: string, add: Canister[]): Section {
  if (!add.length) return section;
  const idx = section.stands.findIndex((s) => s.name === standName);
  if (idx >= 0) {
    const stands = section.stands.map((s, i) => {
      if (i !== idx) return s;
      const merged = [...s.canisters];
      for (const c of add) if (!merged.some((x) => canisterMatches(x, c))) merged.push(c);
      return { ...s, canisters: sortCanistersForDisplay(merged) };
    });
    return { ...section, stands };
  }
  const stand: Stand = {
    name: standName,
    description,
    commander_principal: '',
    canisters: sortCanistersForDisplay(add),
  };
  const stands = standName === CASALS_CONDUCTOR_STAND ? [stand, ...section.stands] : [...section.stands, stand];
  return { ...section, stands };
}

/**
 * Casals section first, its canisters carrying live controllers.
 *
 * With a current backend the tree already contains `Casals/conductor` and
 * `Casals/governance`; this only coalesces `controllers` onto the backend and
 * frontend rows and moves the section to the top. Older trees (conductor rows
 * without a stand, multisig on `System/governance`) are re-homed client-side.
 */
export function augmentTreeWithCasals(
  tree: Tree,
  backendId: string,
  frontendId: string,
  controllers: { backend?: string[]; frontend?: string[] } = {},
): Tree {
  let casals: Section | null = null;
  const rest: Section[] = [];
  let backend: Canister | null = null;
  let frontend: Canister | null = null;
  let multisig: Canister | null = null;
  let casalsHasMultisig = false;

  const patch = (c: Canister): Canister => {
    const role = coreRole(c, backendId, frontendId);
    if (role === 'backend') {
      const p = { ...c, controllers: coalesceControllers(c.controllers, controllers.backend) };
      backend ??= p;
      return p;
    }
    if (role === 'frontend') {
      const p = { ...c, controllers: coalesceControllers(c.controllers, controllers.frontend) };
      frontend ??= p;
      return p;
    }
    return c;
  };

  for (const sec of tree.sections) {
    if (isCasalsSectionName(sec.name)) {
      const stands = sec.stands.map((stand) => {
        const canisters = stand.canisters.map(patch);
        if (canisters.some((c) => isMultisigCanister(c))) casalsHasMultisig = true;
        return { ...stand, canisters };
      });
      casals = casals ? { ...casals, stands: [...casals.stands, ...stands] } : { ...sec, stands };
      continue;
    }
    // Legacy trees: core canisters parked on other sections are pulled out.
    const stands: Stand[] = [];
    for (const stand of sec.stands) {
      const canisters: Canister[] = [];
      for (const c of stand.canisters) {
        const role = coreRole(c, backendId, frontendId);
        if (role === 'multisig') {
          multisig ??= c;
          continue;
        }
        if (role) {
          patch(c);
          continue;
        }
        canisters.push(c);
      }
      if (canisters.length || !stand.canisters.length) stands.push({ ...stand, canisters });
    }
    if (stands.length || !sec.stands.length) rest.push({ ...sec, stands });
  }

  const inCasals = (c: Canister | null) =>
    !!c && !!casals && casals.stands.some((s) => s.canisters.some((x) => canisterMatches(x, c)));

  const conductorAdd: Canister[] = [];
  const be = backend ?? (backendId ? syntheticCasalsBackend(backendId, controllers.backend) : null);
  const fe = frontend ?? (frontendId ? syntheticCasalsFrontend(frontendId, controllers.frontend) : null);
  if (be && !inCasals(be)) conductorAdd.push(be);
  if (fe && !inCasals(fe)) conductorAdd.push(fe);
  const governanceAdd: Canister[] = multisig && !casalsHasMultisig ? [multisig] : [];

  if (!casals && !conductorAdd.length && !governanceAdd.length) return tree;

  let section: Section = casals ?? {
    name: 'Casals',
    description: 'Casals system canisters',
    commander_principal: '',
    stands: [],
  };
  section = withStandCanisters(
    section, CASALS_CONDUCTOR_STAND, 'Conductor: backend, frontend and WASM store', conductorAdd,
  );
  section = withStandCanisters(
    section, CASALS_GOVERNANCE_STAND, 'Orchestration governance: multisig', governanceAdd,
  );

  return { ...tree, sections: [section, ...rest] };
}

/** @deprecated Use augmentTreeWithCasals */
export const augmentTreeWithConductor = augmentTreeWithCasals;

