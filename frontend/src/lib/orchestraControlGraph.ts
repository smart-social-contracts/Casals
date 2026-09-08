/** Build a control-authority graph from orchestra tree + orchestration status. */

import type { Canister, CommanderGrant, OrchestrationStatus, Section, Stand, Tree } from './api';

export type ControlEdgeType =
  | 'ic_controller'
  | 'casals_commander'
  | 'baton_top_commander'
  | 'baton_commander'
  | 'baton_manages'
  | 'baton_ic_control';

export interface ControlGraphLayers {
  icControllers: boolean;
  commanders: boolean;
  baton: boolean;
}

export const DEFAULT_CONTROL_GRAPH_LAYERS: ControlGraphLayers = {
  icControllers: true,
  commanders: true,
  baton: true,
};

export interface ControlNode {
  id: string;
  kind: 'canister' | 'principal';
  label: string;
  sublabel?: string;
  canister?: Canister;
  principal?: string;
  rank: number;
  section?: string;
  stand?: string;
}

export interface ControlEdge {
  id: string;
  type: ControlEdgeType;
  from: string;
  to: string;
  label?: string;
}

export interface ControlGraph {
  nodes: ControlNode[];
  edges: ControlEdge[];
  warnings: string[];
}

export interface ControlGraphOptions {
  layers?: Partial<ControlGraphLayers>;
  casalsBackendId?: string;
  principalLabel?: (principal: string) => string;
}

export interface BatonRef {
  name: string;
  canister_id: string;
  section?: string;
  stand?: string;
  managed_canisters?: string[];
  casals_is_commander?: boolean;
}

const BATON_WASM_PREFIX = 'orchestration-baton';
const MULTISIG_WASM_PREFIX = 'orchestration-multisig';

function isBatonWasm(wasmKey?: string): boolean {
  const k = wasmKey ?? '';
  return k === BATON_WASM_PREFIX || k.startsWith(`${BATON_WASM_PREFIX}@`);
}

function isMultisigWasm(wasmKey?: string): boolean {
  const k = wasmKey ?? '';
  return k === MULTISIG_WASM_PREFIX || k.startsWith(`${MULTISIG_WASM_PREFIX}@`);
}

function isMultisigCanister(c: Pick<Canister, 'wasm_key' | 'name'>): boolean {
  return isMultisigWasm(c.wasm_key) || c.name === 'multisig';
}

function isBatonCanister(c: Pick<Canister, 'wasm_key'>): boolean {
  return isBatonWasm(c.wasm_key);
}

function isCasalsCanister(c: Pick<Canister, 'name'>): boolean {
  return c.name === 'casals-backend' || c.name === 'casals-frontend';
}

function batonControlsTarget(
  tree: Tree,
  batonCanisterId: string,
  targetCanisterId: string,
): boolean {
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id === targetCanisterId) {
          return (c.controllers ?? []).some((p) => p === batonCanisterId);
        }
      }
    }
  }
  return false;
}

export interface NodePosition {
  x: number;
  y: number;
}

export const CONTROL_EDGE_META: Record<
  ControlEdgeType,
  { label: string; tooltip: string; stroke: string; dash?: string; width?: number }
> = {
  ic_controller: {
    label: 'IC controller',
    tooltip: 'IC controller — can call install_code / update_settings on the target',
    stroke: '#4f46e5',
    width: 2,
  },
  casals_commander: {
    label: 'Casals commander',
    tooltip: 'Casals commander — delegated permissions on this section or stand',
    stroke: '#64748b',
    dash: '6 4',
    width: 1.5,
  },
  baton_top_commander: {
    label: 'Baton top commander',
    tooltip: 'Baton top commander — configures baton commanders and upgrade policy',
    stroke: '#ea580c',
    dash: '2 4',
    width: 1.5,
  },
  baton_commander: {
    label: 'Baton commander',
    tooltip: 'Baton commander — can propose managed upgrades',
    stroke: '#f97316',
    dash: '2 4',
    width: 1.5,
  },
  baton_manages: {
    label: 'Baton manages',
    tooltip: 'Baton managed set — upgrades flow through this baton',
    stroke: '#fb923c',
    dash: '8 4',
    width: 1.5,
  },
  baton_ic_control: {
    label: 'Baton IC control',
    tooltip: 'Baton is also an IC co-controller of this canister',
    stroke: '#c2410c',
    width: 2.5,
  },
};

function entityCommanders(
  entity: Pick<Section | Stand, 'commanders' | 'commander_principal'>,
): CommanderGrant[] {
  if (entity.commanders?.length) return entity.commanders;
  const p = (entity.commander_principal || '').trim();
  if (!p) return [];
  return [{ principal: p }];
}

function canisterNodeId(canisterId: string): string {
  return `canister:${canisterId}`;
}

function principalNodeId(principal: string): string {
  return `principal:${principal}`;
}

function computeCanisterRank(c: Canister): number {
  if (isMultisigCanister(c)) return 0;
  if (isCasalsCanister(c)) return 1;
  if (isBatonCanister(c)) return 2;
  if (c.kind === 'frontend') return 4;
  return 3;
}

/**
 * IC controller lists are symmetric co-control — a lower tier (e.g. casals-backend)
 * may appear on multisig's controllers. For the governance graph, only show downhill
 * edges along multisig → casals → baton → realm canisters.
 */
export function isUphillOrchestraIcEdge(from: ControlNode, to: ControlNode): boolean {
  if (from.kind !== 'canister' || to.kind !== 'canister') return false;
  return from.rank > to.rank;
}

function standCommanderTarget(stand: Stand): string | null {
  const backends = stand.canisters.filter(
    (c) =>
      c.kind === 'backend' &&
      c.canister_id &&
      !isBatonWasm(c.wasm_key) &&
      !isMultisigWasm(c.wasm_key),
  );
  const named = backends.find((c) => c.name.endsWith('-backend'));
  return named?.canister_id ?? backends[0]?.canister_id ?? null;
}

function findCanisterByPrincipal(
  tree: Tree,
  principal: string,
): { canister: Canister; section: string; stand: string } | null {
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id === principal) {
          return { canister: c, section: sec.name, stand: stand.name };
        }
      }
    }
  }
  return null;
}

export function buildControlGraph(
  tree: Tree | null | undefined,
  orchestrationStatus: OrchestrationStatus | null | undefined,
  batons: BatonRef[],
  options: ControlGraphOptions = {},
): ControlGraph {
  const layers: ControlGraphLayers = {
    ...DEFAULT_CONTROL_GRAPH_LAYERS,
    ...options.layers,
  };
  const warnings: string[] = [];
  if (!tree) return { nodes: [], edges: [], warnings };

  const nodes = new Map<string, ControlNode>();
  const edges: ControlEdge[] = [];
  const edgeKeys = new Set<string>();
  let missingControllers = 0;

  const labelFor = (principal: string) => options.principalLabel?.(principal) ?? principal.slice(0, 12);

  function addEdge(type: ControlEdgeType, from: string, to: string, label?: string): void {
    if (!from || !to || from === to) return;
    const fromNode = nodes.get(from);
    const toNode = nodes.get(to);
    if (
      (type === 'ic_controller' || type === 'baton_ic_control') &&
      fromNode &&
      toNode &&
      isUphillOrchestraIcEdge(fromNode, toNode)
    ) {
      return;
    }
    const key = `${type}|${from}|${to}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    edges.push({ id: key, type, from, to, label });
  }

  function ensureCanisterNode(c: Canister, section?: string, stand?: string): string {
    const id = canisterNodeId(c.canister_id);
    if (!nodes.has(id)) {
      nodes.set(id, {
        id,
        kind: 'canister',
        label: c.name,
        sublabel: c.kind,
        canister: c,
        rank: computeCanisterRank(c),
        section,
        stand,
      });
    }
    return id;
  }

  function resolveNodeId(principal: string): string {
    const found = findCanisterByPrincipal(tree, principal);
    if (found) {
      return ensureCanisterNode(found.canister, found.section, found.stand);
    }
    const id = principalNodeId(principal);
    if (!nodes.has(id)) {
      nodes.set(id, {
        id,
        kind: 'principal',
        label: labelFor(principal),
        sublabel: 'principal',
        principal,
        rank: 1,
      });
    }
    return id;
  }

  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (!c.canister_id) continue;
        const targetId = ensureCanisterNode(c, sec.name, stand.name);

        if (layers.icControllers) {
          const controllers = c.controllers ?? [];
          if (!controllers.length) missingControllers += 1;
          for (const p of controllers) {
            addEdge('ic_controller', resolveNodeId(p), targetId);
          }
        }
      }

      if (layers.commanders) {
        const target = standCommanderTarget(stand);
        if (target) {
          const targetId = canisterNodeId(target);
          for (const cmd of entityCommanders(stand)) {
            if (!cmd.principal) continue;
            addEdge('casals_commander', resolveNodeId(cmd.principal), targetId, stand.name);
          }
        }
      }
    }

    if (layers.commanders) {
      for (const cmd of entityCommanders(sec)) {
        if (!cmd.principal) continue;
        const from = resolveNodeId(cmd.principal);
        for (const stand of sec.stands) {
          const target = standCommanderTarget(stand);
          if (!target) continue;
          addEdge('casals_commander', from, canisterNodeId(target), sec.name);
        }
      }
    }
  }

  if (layers.baton) {
    const statusBatons = orchestrationStatus?.batons ?? [];
    const byId = new Map<string, BatonRef>();
    for (const b of batons) {
      if (b.canister_id) byId.set(b.canister_id, b);
    }
    for (const b of statusBatons) {
      if (!b.canister_id) continue;
      const existing = byId.get(b.canister_id);
      byId.set(b.canister_id, {
        name: b.name,
        canister_id: b.canister_id,
        section: b.section ?? existing?.section,
        stand: b.stand ?? existing?.stand,
        managed_canisters: b.managed_canisters ?? existing?.managed_canisters,
        casals_is_commander: b.casals_is_commander ?? existing?.casals_is_commander,
      });
    }

    for (const b of byId.values()) {
      if (!b.canister_id) continue;
      const batonId = canisterNodeId(b.canister_id);
      const statusEntry = statusBatons.find((x) => x.canister_id === b.canister_id);
      const config = statusEntry?.config ?? {};
      const topCommander =
        (typeof config.top_commander === 'string' && config.top_commander) ||
        options.casalsBackendId ||
        '';
      if (topCommander) {
        addEdge('baton_top_commander', resolveNodeId(topCommander), batonId);
      }

      const commanders =
        statusEntry?.commanders ??
        orchestrationStatus?.commanders ??
        [];
      for (const cmd of commanders) {
        if (!cmd.principal) continue;
        addEdge('baton_commander', resolveNodeId(cmd.principal), batonId);
      }

      const managed = b.managed_canisters ?? [];
      for (const mid of managed) {
        if (!mid) continue;
        const managedId = canisterNodeId(mid);
        addEdge('baton_manages', batonId, managedId);
        if (layers.icControllers && batonControlsTarget(tree, b.canister_id, mid)) {
          addEdge('baton_ic_control', batonId, managedId);
        }
      }
    }

    if (!byId.size && tree.sections.some((s) => s.stands.some((d) => d.canisters.some(isBatonCanister)))) {
      warnings.push('Baton canisters exist but orchestration_status returned no baton detail.');
    }
  }

  if (layers.icControllers && missingControllers > 0) {
    warnings.push(
      `${missingControllers} canister(s) have no cached IC controllers — refresh the controller cache from a canister row in Tree view.`,
    );
  }

  const activeEdges = edges.filter((e) => {
    if (e.type === 'ic_controller' || e.type === 'baton_ic_control') return layers.icControllers;
    if (e.type === 'casals_commander') return layers.commanders;
    return layers.baton;
  });

  const activeNodeIds = new Set<string>();
  for (const e of activeEdges) {
    activeNodeIds.add(e.from);
    activeNodeIds.add(e.to);
  }

  const activeNodes = [...nodes.values()].filter((n) => activeNodeIds.has(n.id));
  return { nodes: activeNodes, edges: activeEdges, warnings };
}

export function layoutControlGraph(
  graph: ControlGraph,
  width: number,
  nodeWidth = 168,
  rowHeight = 108,
): Map<string, NodePosition> {
  const positions = new Map<string, NodePosition>();
  const byRank = new Map<number, ControlNode[]>();
  for (const node of graph.nodes) {
    const row = byRank.get(node.rank) ?? [];
    row.push(node);
    byRank.set(node.rank, row);
  }

  const ranks = [...byRank.keys()].sort((a, b) => a - b);
  for (const rank of ranks) {
    const row = byRank.get(rank) ?? [];
    row.sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
    const totalWidth = Math.max(row.length * nodeWidth, width);
    const startX = Math.max((width - row.length * nodeWidth) / 2 + nodeWidth / 2, nodeWidth / 2);
    row.forEach((node, index) => {
      positions.set(node.id, {
        x: startX + index * nodeWidth,
        y: 56 + rank * rowHeight,
      });
    });
  }

  return positions;
}

export const CONTROL_NODE_WIDTH = 148;
export const CONTROL_NODE_HEIGHT = 56;

export interface EdgeAnchors {
  from: NodePosition;
  to: NodePosition;
}

/** Pick border anchor points based on relative node placement. */
export function edgeAnchors(
  fromPos: NodePosition,
  toPos: NodePosition,
  width = CONTROL_NODE_WIDTH,
  height = CONTROL_NODE_HEIGHT,
): EdgeAnchors {
  const dx = toPos.x - fromPos.x;
  const dy = toPos.y - fromPos.y;

  function anchor(center: NodePosition, outDx: number, outDy: number): NodePosition {
    const hw = width / 2;
    const hh = height / 2;
    if (Math.abs(outDy) >= Math.abs(outDx) * 0.85) {
      return { x: center.x, y: center.y + (outDy >= 0 ? hh : -hh) };
    }
    return { x: center.x + (outDx >= 0 ? hw : -hw), y: center.y };
  }

  return {
    from: anchor(fromPos, dx, dy),
    to: anchor(toPos, -dx, -dy),
  };
}

/** Smooth bezier between two anchor points (updates as nodes move). */
export function edgePathBetween(anchors: EdgeAnchors): string {
  const { from, to } = anchors;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy);
  const curve = Math.min(Math.max(dist * 0.38, 28), 140);

  if (Math.abs(dy) >= Math.abs(dx) * 0.85) {
    const c1y = from.y + (dy >= 0 ? curve : -curve);
    const c2y = to.y + (dy >= 0 ? -curve : curve);
    return `M ${from.x} ${from.y} C ${from.x} ${c1y}, ${to.x} ${c2y}, ${to.x} ${to.y}`;
  }

  const c1x = from.x + (dx >= 0 ? curve : -curve);
  const c2x = to.x + (dx >= 0 ? -curve : curve);
  return `M ${from.x} ${from.y} C ${c1x} ${from.y}, ${c2x} ${to.y}, ${to.x} ${to.y}`;
}

export function graphViewport(
  positions: Map<string, NodePosition>,
  minCanvasWidth = 720,
  padding = 56,
  nodeWidth = CONTROL_NODE_WIDTH,
  nodeHeight = CONTROL_NODE_HEIGHT,
): { minX: number; minY: number; width: number; height: number } {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const pos of positions.values()) {
    minX = Math.min(minX, pos.x - nodeWidth / 2);
    minY = Math.min(minY, pos.y - nodeHeight / 2);
    maxX = Math.max(maxX, pos.x + nodeWidth / 2);
    maxY = Math.max(maxY, pos.y + nodeHeight / 2);
  }

  if (!Number.isFinite(minX)) {
    return { minX: 0, minY: 0, width: minCanvasWidth, height: 360 };
  }

  const width = Math.max(maxX - minX + padding * 2, minCanvasWidth);
  const height = Math.max(maxY - minY + padding * 2, 320);
  return {
    minX: minX - padding,
    minY: minY - padding,
    width,
    height,
  };
}

/** Fixed canvas from auto-layout only — does not shift while nodes are dragged. */
export function frozenGraphViewport(
  positions: Map<string, NodePosition>,
  minCanvasWidth = 720,
  padding = 56,
  dragMargin = 320,
  nodeWidth = CONTROL_NODE_WIDTH,
  nodeHeight = CONTROL_NODE_HEIGHT,
): { minX: number; minY: number; width: number; height: number } {
  const hw = nodeWidth / 2;
  const hh = nodeHeight / 2;
  let maxX = padding + dragMargin;
  let maxY = padding + dragMargin;

  for (const pos of positions.values()) {
    maxX = Math.max(maxX, pos.x + hw + dragMargin);
    maxY = Math.max(maxY, pos.y + hh + dragMargin);
  }

  if (positions.size === 0) {
    return { minX: 0, minY: 0, width: minCanvasWidth, height: 360 };
  }

  return {
    minX: 0,
    minY: 0,
    width: Math.max(maxX + padding, minCanvasWidth),
    height: Math.max(maxY + padding, 320),
  };
}

export function graphLayoutSignature(graph: ControlGraph, layoutWidth: number): string {
  return `${layoutWidth}|${graph.nodes.map((n) => n.id).sort().join(',')}`;
}

export const CONTROL_EDGE_TYPE_GROUPS: Record<keyof ControlGraphLayers, ControlEdgeType[]> = {
  icControllers: ['ic_controller', 'baton_ic_control'],
  commanders: ['casals_commander'],
  baton: ['baton_top_commander', 'baton_commander', 'baton_manages'],
};

export type ControlEdgeTypeVisibility = Record<ControlEdgeType, boolean>;

export const DEFAULT_CONTROL_EDGE_TYPE_VISIBILITY: ControlEdgeTypeVisibility = {
  ic_controller: true,
  casals_commander: true,
  baton_top_commander: true,
  baton_commander: true,
  baton_manages: true,
  baton_ic_control: true,
};

export function layerGroupEnabled(
  types: ControlEdgeTypeVisibility,
  group: keyof ControlGraphLayers,
): boolean {
  return CONTROL_EDGE_TYPE_GROUPS[group].some((t) => types[t]);
}

export function setLayerGroupVisibility(
  types: ControlEdgeTypeVisibility,
  group: keyof ControlGraphLayers,
  enabled: boolean,
): ControlEdgeTypeVisibility {
  const next = { ...types };
  for (const t of CONTROL_EDGE_TYPE_GROUPS[group]) {
    next[t] = enabled;
  }
  return next;
}

/** Hide edges whose type is toggled off; drop orphan nodes. */
export function filterControlGraphByEdgeTypes(
  graph: ControlGraph,
  types: ControlEdgeTypeVisibility,
): ControlGraph {
  const edges = graph.edges.filter((e) => types[e.type]);
  const activeNodeIds = new Set<string>();
  for (const e of edges) {
    activeNodeIds.add(e.from);
    activeNodeIds.add(e.to);
  }
  const nodes = graph.nodes.filter((n) => activeNodeIds.has(n.id));
  return { nodes, edges, warnings: graph.warnings };
}

export function standVisibilityKey(section: string, stand: string): string {
  return `${section}|${stand}`;
}

export interface ControlGraphVisibility {
  hiddenSections: ReadonlySet<string>;
  hiddenStands: ReadonlySet<string>;
  hiddenCanisters: ReadonlySet<string>;
}

export const EMPTY_CONTROL_GRAPH_VISIBILITY: ControlGraphVisibility = {
  hiddenSections: new Set(),
  hiddenStands: new Set(),
  hiddenCanisters: new Set(),
};

export function isNodeHiddenByScope(node: ControlNode, vis: ControlGraphVisibility): boolean {
  if (node.canister?.canister_id && vis.hiddenCanisters.has(node.canister.canister_id)) {
    return true;
  }
  if (node.section && vis.hiddenSections.has(node.section)) return true;
  if (node.section && node.stand) {
    if (vis.hiddenStands.has(standVisibilityKey(node.section, node.stand))) return true;
  }
  return false;
}

/** Hide scoped canisters (and principals only connected to hidden canisters). */
export function filterControlGraph(
  graph: ControlGraph,
  vis: ControlGraphVisibility,
): ControlGraph {
  const canisterVisible = (node: ControlNode): boolean =>
    node.kind === 'canister' && !isNodeHiddenByScope(node, vis);

  const edges = graph.edges.filter((e) => {
    const fromNode = graph.nodes.find((n) => n.id === e.from);
    const toNode = graph.nodes.find((n) => n.id === e.to);
    if (!fromNode || !toNode) return false;
    if (fromNode.kind === 'canister' && !canisterVisible(fromNode)) return false;
    if (toNode.kind === 'canister' && !canisterVisible(toNode)) return false;
    return true;
  });

  const activeIds = new Set<string>();
  for (const e of edges) {
    activeIds.add(e.from);
    activeIds.add(e.to);
  }

  const nodes = graph.nodes.filter((n) => {
    if (isNodeHiddenByScope(n, vis)) return false;
    return activeIds.has(n.id);
  });

  return { nodes, edges, warnings: graph.warnings };
}

export function graphDimensions(
  graph: ControlGraph,
  width: number,
  nodeWidth = 168,
  rowHeight = 108,
): { width: number; height: number } {
  const maxRank = graph.nodes.reduce((m, n) => Math.max(m, n.rank), 0);
  const maxRow = Math.max(
    1,
    ...[...new Set(graph.nodes.map((n) => n.rank))].map(
      (rank) => graph.nodes.filter((n) => n.rank === rank).length,
    ),
  );
  return {
    width: Math.max(width, maxRow * nodeWidth + 48),
    height: 56 + (maxRank + 1) * rowHeight + 48,
  };
}
