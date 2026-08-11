/** Orchestra governance helpers — multisig, batons, controllers, managed-canister links. */

import type { Canister, Tree, Section, Stand, OrchestrationStatus, BatonStatus } from './api';
import {
  isBatonWasm,
  isMultisigWasm,
  batonControlsTarget,
  batonConsoleUrl,
  multisigConsoleUrl,
} from './orchestrationNav';
import { controllerLabel } from './controllerLabels';

export interface LocatedCanister extends Canister {
  section: string;
  stand: string;
}

export interface BatonRef {
  name: string;
  canister_id: string;
  section?: string;
  stand?: string;
  managed_canisters?: string[];
  casals_is_commander?: boolean;
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

export function resolveBatons(
  status: OrchestrationStatus | null | undefined,
  tree: Tree | null | undefined,
): BatonRef[] {
  if (status?.batons?.length) {
    return status.batons.map((b) => ({
      name: b.name,
      canister_id: b.canister_id,
      section: b.section,
      stand: b.stand,
      managed_canisters: b.managed_canisters,
      casals_is_commander: b.casals_is_commander,
    }));
  }
  if (status?.baton?.canister_id) {
    return [{
      name: status.baton.name,
      canister_id: status.baton.canister_id,
      managed_canisters: status.managed_canisters,
      casals_is_commander: status.casals_is_commander,
    }];
  }
  return findBatonsInTree(tree).map((c) => ({
    name: c.name,
    canister_id: c.canister_id,
    section: c.section,
    stand: c.stand,
  }));
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
    if (b.managed_canisters?.includes(canister.canister_id)) return b;
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

export function mergeBatonStatus(
  treeBatons: LocatedCanister[],
  statusBatons: BatonRef[],
): BatonRef[] {
  const byId = new Map<string, BatonRef>();
  for (const b of statusBatons) {
    if (b.canister_id) byId.set(b.canister_id, b);
  }
  for (const c of treeBatons) {
    if (!c.canister_id) continue;
    const existing = byId.get(c.canister_id);
    byId.set(c.canister_id, {
      name: c.name,
      canister_id: c.canister_id,
      section: c.section,
      stand: c.stand,
      managed_canisters: existing?.managed_canisters,
      casals_is_commander: existing?.casals_is_commander,
    });
  }
  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
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

export function isCasalsCanister(c: Pick<Canister, 'name'>): boolean {
  return c.name === 'casals-backend' || c.name === 'casals-frontend';
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
  return name === 'Casals' || name === 'Conductor';
}

function canisterMatches(a: Canister, b: Canister): boolean {
  if (a.canister_id && b.canister_id && a.canister_id === b.canister_id) return true;
  if (a.name && b.name && a.name === b.name) return true;
  return false;
}

function sectionHasCanister(section: Section, canister: Canister): boolean {
  return section.stands.some((stand) => stand.canisters.some((c) => canisterMatches(c, canister)));
}

function sectionHasMultisig(section: Section): boolean {
  return section.stands.some((stand) => stand.canisters.some((c) => isMultisigCanister(c)));
}

function peelCasalsCanisters(
  canisters: Canister[],
  backendId: string,
  frontendId: string,
  found: { backend: Canister | null; frontend: Canister | null },
): Canister[] {
  const kept = [];
  for (const c of canisters) {
    if ((backendId && c.canister_id === backendId) || c.name === 'casals-backend') {
      if (!found.backend) found.backend = c;
      continue;
    }
    if ((frontendId && c.canister_id === frontendId) || c.name === 'casals-frontend') {
      if (!found.frontend) found.frontend = c;
      continue;
    }
    kept.push(c);
  }
  return kept;
}

/** Pull multisig and Casals front/back out of the tree for re-homing under Casals. */
function extractCasalsCanisters(
  tree: Tree,
  backendId: string,
  frontendId: string,
): {
  multisig: Canister | null;
  backend: Canister | null;
  frontend: Canister | null;
  existingCasals: Section | null;
  rest: Tree;
} {
  let multisig: Canister | null = null;
  let backend: Canister | null = null;
  let frontend: Canister | null = null;
  let existingCasals: Section | null = null;

  const sections = [];
  for (const sec of tree.sections) {
    if (isCasalsSectionName(sec.name)) {
      const stands = [];
      for (const stand of sec.stands) {
        const found = { backend, frontend };
        const canisters = peelCasalsCanisters(stand.canisters, backendId, frontendId, found);
        backend = found.backend;
        frontend = found.frontend;
        for (const c of canisters) {
          if (isMultisigCanister(c) && !multisig) multisig = c;
        }
        if (canisters.length) stands.push({ ...stand, canisters });
      }
      if (stands.length) existingCasals = { ...sec, stands };
      continue;
    }

    const stands = [];
    for (const stand of sec.stands) {
      const canisters = [];
      for (const c of stand.canisters) {
        if (isMultisigCanister(c)) {
          if (!multisig) multisig = c;
          continue;
        }
        if ((backendId && c.canister_id === backendId) || c.name === 'casals-backend') {
          if (!backend) backend = c;
          continue;
        }
        if ((frontendId && c.canister_id === frontendId) || c.name === 'casals-frontend') {
          if (!frontend) frontend = c;
          continue;
        }
        canisters.push(c);
      }
      if (canisters.length) stands.push({ ...stand, canisters });
    }
    if (stands.length) sections.push({ ...sec, stands });
  }

  return {
    multisig,
    backend,
    frontend,
    existingCasals,
    rest: { sections, principal_aliases: tree.principal_aliases },
  };
}

function resolveCasalsBackend(
  fromTree: Canister | null,
  backendId: string,
  controllers?: string[],
): Canister | null {
  if (fromTree) {
    return { ...fromTree, controllers: fromTree.controllers ?? controllers };
  }
  if (!backendId) return null;
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

function resolveCasalsFrontend(
  fromTree: Canister | null,
  frontendId: string,
  controllers?: string[],
): Canister | null {
  if (fromTree) {
    return { ...fromTree, controllers: fromTree.controllers ?? controllers };
  }
  if (!frontendId) return null;
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

function mergeCanistersIntoStand(stand: Stand, additions: Canister[]): Stand {
  const merged = [...stand.canisters];
  for (const c of additions) {
    if (!merged.some((existing) => canisterMatches(existing, c))) merged.push(c);
  }
  return { ...stand, canisters: sortCanistersForDisplay(merged) };
}

/** Merge extracted governance canisters into an existing Casals section. */
function augmentExistingCasalsSection(
  section: Section,
  multisig: Canister | null,
  backend: Canister | null,
  frontend: Canister | null,
): Section {
  const additions: Canister[] = [];
  if (multisig && !sectionHasMultisig(section) && !sectionHasCanister(section, multisig)) {
    additions.push(multisig);
  }
  if (backend && !sectionHasCanister(section, backend)) additions.push(backend);
  if (frontend && !sectionHasCanister(section, frontend)) additions.push(frontend);
  if (!additions.length) return section;

  if (!section.stands.length) {
    return {
      ...section,
      stands: [{
        name: 'System',
        description: 'Multisig, backend, and frontend',
        commander_principal: '',
        canisters: sortCanistersForDisplay(additions),
      }],
    };
  }

  const systemIdx = section.stands.findIndex((stand) => stand.name === 'System');
  const targetIdx = systemIdx >= 0 ? systemIdx : 0;
  const stands = section.stands.map((stand, idx) =>
    idx === targetIdx ? mergeCanistersIntoStand(stand, additions) : stand,
  );
  return { ...section, stands };
}

/** Prepend Casals section (multisig + front/back) above Deployments, Infra, etc. */
export function augmentTreeWithCasals(
  tree: Tree,
  backendId: string,
  frontendId: string,
  controllers: { backend?: string[]; frontend?: string[] } = {},
): Tree {
  const {
    multisig,
    backend: backendFromTree,
    frontend: frontendFromTree,
    existingCasals,
    rest,
  } = extractCasalsCanisters(tree, backendId, frontendId);

  const backend = resolveCasalsBackend(backendFromTree, backendId, controllers.backend);
  const frontend = resolveCasalsFrontend(frontendFromTree, frontendId, controllers.frontend);

  if (existingCasals) {
    const casalsSection = augmentExistingCasalsSection(existingCasals, multisig, backend, frontend);
    return { sections: [casalsSection, ...rest.sections], principal_aliases: tree.principal_aliases };
  }

  const casalsCanisters: Canister[] = [];
  if (multisig) casalsCanisters.push(multisig);
  if (backend) casalsCanisters.push(backend);
  if (frontend) casalsCanisters.push(frontend);

  if (!casalsCanisters.length) return tree;

  const casalsSection = {
    name: 'Casals',
    description: 'Casals orchestrator and orchestration governance',
    commander_principal: '',
    stands: [{
      name: 'casals',
      description: 'Multisig, backend, and frontend',
      commander_principal: '',
      canisters: sortCanistersForDisplay(casalsCanisters),
    }],
  };

  return { sections: [casalsSection, ...rest.sections], principal_aliases: tree.principal_aliases };
}

/** @deprecated Use augmentTreeWithCasals */
export const augmentTreeWithConductor = augmentTreeWithCasals;
