import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { CanisterKind, CanisterRow, Grant, Orchestra, OrchestraView, StandView } from './api.ts';
import {
  buildControlGraph,
  clampControlGraphZoom,
  DEFAULT_CONTROL_EDGE_TYPE_VISIBILITY,
  DEFAULT_CONTROL_GRAPH_LAYERS,
  MAX_CONTROL_GRAPH_ZOOM,
  MIN_CONTROL_GRAPH_ZOOM,
  parseControlGraphView,
  serializeControlGraphView,
  edgeAnchors,
  edgePathBetween,
  filterControlGraph,
  filterControlGraphByEdgeTypes,
  frozenGraphViewport,
  graphViewport,
  inferManagedCanistersFromStand,
  inferManagedCanistersFromTree,
  isUphillOrchestraIcEdge,
  layoutControlGraph,
  standVisibilityKey,
} from './orchestraControlGraph.ts';

const CASALS = 'casals-backend-principal';
const MULTISIG = 'multisig-principal';
const BATON = 'baton-principal';
const TENANT_BE = 'tenant-backend-principal';
const TENANT_FE = 'tenant-frontend-principal';
const INSTALLER = 'installer-principal';

function canister(
  section: string,
  stand: string,
  name: string,
  canister_id: string,
  wasm: string,
  controllers: string[],
  kind: CanisterKind = 'backend',
): CanisterRow {
  return {
    name, canister_id, kind, wasm, wasm_hash: '', status: 'ready', public: false, tags: [], controllers,
    cycles: 0, content: '', snapshot: '', stand, section, stand_built_at: 0, build_error: '',
  };
}

const allOn = (scope: string, principal: string): Grant => ({ scope, principal, permissions: ['*'], calls: [] });

function stand(section: string, name: string, canisters: CanisterRow[], grants: Grant[] = []): StandView {
  return { name, section, description: '', built_at: 0, build_error: '', members: [], grants, canisters };
}

function fixtureTree(): OrchestraView {
  const system = [
    canister('Casals', 'System', 'multisig', MULTISIG, 'orchestration-multisig', [MULTISIG]),
    canister('Casals', 'System', 'casals-backend', CASALS, 'casals-backend', [MULTISIG, CASALS]),
  ];
  const tenant = [
    canister('Deployments', 'testtenant7', 'testtenant7-baton', BATON, 'orchestration-baton', [MULTISIG, CASALS, BATON]),
    canister('Deployments', 'testtenant7', 'testtenant7-backend', TENANT_BE, 'tenant-backend', [MULTISIG, CASALS, BATON]),
    canister('Deployments', 'testtenant7', 'testtenant7-frontend', TENANT_FE, 'tenant-assets', [MULTISIG, CASALS, BATON], 'frontend'),
  ];
  return {
    orchestra: { canisters: [...system, ...tenant] } as Orchestra,
    grants: [],
    sections: [
      { name: 'Casals', description: '', template: null, grants: [], stands: [stand('Casals', 'System', system)] },
      {
        name: 'Deployments',
        description: '',
        template: null,
        grants: [allOn('section:Deployments', INSTALLER), allOn('section:Deployments', 'sha256:unclaimed')],
        stands: [stand('Deployments', 'testtenant7', tenant, [allOn('stand:testtenant7', TENANT_BE)])],
      },
    ],
  };
}

function clearControllers(view: OrchestraView): void {
  for (const c of view.orchestra.canisters) c.controllers = [];
}

test('buildControlGraph emits IC controller and grant edges, skipping unclaimed slots', () => {
  const graph = buildControlGraph(fixtureTree(), [], {
    layers: DEFAULT_CONTROL_GRAPH_LAYERS,
    casalsBackendId: CASALS,
  });

  assert.ok(graph.edges.some((e) => e.type === 'ic_controller' && e.to === `canister:${TENANT_BE}`));
  assert.ok(
    graph.edges.some(
      (e) =>
        e.type === 'casals_commander' &&
        e.from === `principal:${INSTALLER}` &&
        e.to === `canister:${TENANT_BE}`,
    ),
  );
  assert.ok(!graph.nodes.some((n) => n.principal?.startsWith('sha256:')));
});

test('buildControlGraph omits uphill IC edges between orchestra tiers', () => {
  const tree = fixtureTree();
  const multisig = tree.sections[0].stands[0].canisters.find((c) => c.name === 'multisig');
  assert.ok(multisig);
  multisig.controllers = [MULTISIG, CASALS];

  const graph = buildControlGraph(tree, [], { casalsBackendId: CASALS });

  assert.ok(
    graph.edges.some(
      (e) => e.type === 'ic_controller' && e.from === `canister:${MULTISIG}` && e.to === `canister:${CASALS}`,
    ),
  );
  assert.ok(
    !graph.edges.some(
      (e) => e.type === 'ic_controller' && e.from === `canister:${CASALS}` && e.to === `canister:${MULTISIG}`,
    ),
  );
});

test('isUphillOrchestraIcEdge detects casals to multisig', () => {
  const from = {
    id: 'canister:casals',
    kind: 'canister' as const,
    label: 'casals-backend',
    rank: 1,
    canister: { name: 'casals-backend', canister_id: 'casals', kind: 'backend' },
  };
  const to = {
    id: 'canister:multisig',
    kind: 'canister' as const,
    label: 'multisig',
    rank: 0,
    canister: { name: 'multisig', canister_id: 'multisig', kind: 'backend', wasm: 'orchestration-multisig' },
  };
  assert.equal(isUphillOrchestraIcEdge(from, to), true);
  assert.equal(isUphillOrchestraIcEdge(to, from), false);
});

test('inferManagedCanistersFromTree reads baton id from IC controllers', () => {
  const tree = fixtureTree();
  const inferred = inferManagedCanistersFromTree(tree, BATON, {
    section: 'Deployments',
    stand: 'testtenant7',
  });
  assert.deepEqual(inferred.sort(), [TENANT_BE, TENANT_FE].sort());
});

test('inferManagedCanistersFromStand links same-stand peers without baton controllers', () => {
  const tree = fixtureTree();
  clearControllers(tree);
  const inferred = inferManagedCanistersFromStand(tree, BATON, {
    section: 'Deployments',
    stand: 'testtenant7',
  });
  assert.deepEqual(inferred.sort(), [TENANT_BE, TENANT_FE].sort());
});

test('buildControlGraph infers baton_manages from stand topology without baton controllers', () => {
  const tree = fixtureTree();
  clearControllers(tree);
  const graph = buildControlGraph(
    tree,
    [{ name: 'testtenant7-baton', canister_id: BATON, section: 'Deployments', stand: 'testtenant7' }],
    { casalsBackendId: CASALS },
  );
  assert.ok(graph.edges.some((e) => e.type === 'baton_manages' && e.to === `canister:${TENANT_BE}`));
  assert.ok(graph.edges.some((e) => e.type === 'baton_manages' && e.to === `canister:${TENANT_FE}`));
});

test('buildControlGraph adds baton edges from IC controllers', () => {
  const graph = buildControlGraph(
    fixtureTree(),
    [{ name: 'testtenant7-baton', canister_id: BATON, section: 'Deployments', stand: 'testtenant7' }],
    { casalsBackendId: CASALS },
  );

  assert.ok(graph.edges.some((e) => e.type === 'baton_top_commander' && e.to === `canister:${BATON}`));
  assert.ok(graph.edges.some((e) => e.type === 'baton_manages' && e.to === `canister:${TENANT_BE}`));
  assert.ok(
    graph.edges.some(
      (e) => e.type === 'ic_controller' && e.from === `canister:${BATON}` && e.to === `canister:${TENANT_BE}`,
    ),
  );
});

test('buildControlGraph respects layer toggles', () => {
  const full = buildControlGraph(fixtureTree(), [], {
    layers: DEFAULT_CONTROL_GRAPH_LAYERS,
  });
  const graph = filterControlGraphByEdgeTypes(full, {
    ic_controller: false,
    casals_commander: true,
    baton_top_commander: false,
    baton_manages: false,
  });
  assert.equal(graph.edges.every((e) => e.type === 'casals_commander'), true);
});

test('filterControlGraphByEdgeTypes can hide a single edge type', () => {
  const full = buildControlGraph(
    fixtureTree(),
    [{ name: 'testtenant7-baton', canister_id: BATON, section: 'Deployments', stand: 'testtenant7' }],
    { casalsBackendId: CASALS },
  );
  const graph = filterControlGraphByEdgeTypes(full, {
    ic_controller: true,
    casals_commander: true,
    baton_top_commander: true,
    baton_manages: false,
  });
  assert.ok(full.edges.some((e) => e.type === 'baton_manages'));
  assert.ok(!graph.edges.some((e) => e.type === 'baton_manages'));
});

test('layoutControlGraph assigns positions by rank', () => {
  const graph = buildControlGraph(fixtureTree(), [], { casalsBackendId: CASALS });
  const positions = layoutControlGraph(graph, 900);
  const multisig = positions.get(`canister:${MULTISIG}`);
  const frontend = positions.get(`canister:${TENANT_FE}`);
  assert.ok(multisig && frontend);
  assert.ok(multisig.y < frontend.y);
});

test('edgeAnchors connect on the facing box sides', () => {
  const anchors = edgeAnchors({ x: 100, y: 100 }, { x: 100, y: 220 });
  assert.ok(anchors.from.y < anchors.to.y);
  assert.equal(anchors.from.x, anchors.to.x);
});

test('edgePathBetween returns a cubic path', () => {
  const path = edgePathBetween({
    from: { x: 10, y: 10 },
    to: { x: 10, y: 100 },
  });
  assert.match(path, /^M /);
  assert.match(path, / C /);
});

test('graphViewport expands when nodes are spread out', () => {
  const positions = new Map([
    ['a', { x: 0, y: 0 }],
    ['b', { x: 500, y: 400 }],
  ]);
  const vp = graphViewport(positions, 720);
  assert.ok(vp.width >= 720);
  assert.ok(vp.height >= 320);
});

test('frozenGraphViewport keeps a fixed origin while dragging', () => {
  const auto = new Map([
    ['a', { x: 200, y: 100 }],
    ['b', { x: 400, y: 300 }],
  ]);
  const frozen = frozenGraphViewport(auto, 720);
  assert.equal(frozen.minX, 0);
  assert.equal(frozen.minY, 0);
  assert.ok(frozen.width >= 720);
});

test('filterControlGraph hides canisters in a disabled section', () => {
  const full = buildControlGraph(fixtureTree(), [], { casalsBackendId: CASALS });
  const filtered = filterControlGraph(full, {
    hiddenSections: new Set(['Deployments']),
    hiddenStands: new Set(),
    hiddenCanisters: new Set(),
    hiddenPrincipals: new Set(),
  });
  assert.ok(!filtered.nodes.some((n) => n.canister?.canister_id === TENANT_BE));
  assert.ok(filtered.nodes.some((n) => n.canister?.canister_id === CASALS));
});

test('filterControlGraph hides a single canister', () => {
  const full = buildControlGraph(fixtureTree(), [], { casalsBackendId: CASALS });
  const filtered = filterControlGraph(full, {
    hiddenSections: new Set(),
    hiddenStands: new Set(),
    hiddenCanisters: new Set([TENANT_FE]),
    hiddenPrincipals: new Set(),
  });
  assert.ok(!filtered.nodes.some((n) => n.canister?.canister_id === TENANT_FE));
});

test('filterControlGraph hides a principal node', () => {
  const full = buildControlGraph(fixtureTree(), [], { casalsBackendId: CASALS });
  const filtered = filterControlGraph(full, {
    hiddenSections: new Set(),
    hiddenStands: new Set(),
    hiddenCanisters: new Set(),
    hiddenPrincipals: new Set([INSTALLER]),
  });
  assert.ok(!filtered.nodes.some((n) => n.principal === INSTALLER));
  assert.ok(!filtered.edges.some((e) => e.from === `principal:${INSTALLER}`));
});

test('a saved view round-trips visibility, edge layers, zoom, and positions', () => {
  const view = serializeControlGraphView({
    name: 'baton only',
    zoom: 1.4,
    edgeTypes: { ...DEFAULT_CONTROL_EDGE_TYPE_VISIBILITY, ic_controller: false },
    hiddenSections: new Set(['Casals']),
    hiddenStands: new Set(['Deployments|testtenant7']),
    hiddenCanisters: new Set([TENANT_FE]),
    hiddenPrincipals: new Set([INSTALLER]),
    positions: { [`canister:${TENANT_BE}`]: { x: 120, y: 340 } },
  });

  const parsed = parseControlGraphView(JSON.stringify(view));
  assert.equal(parsed.name, 'baton only');
  assert.equal(parsed.zoom, 1.4);
  assert.equal(parsed.edgeTypes.ic_controller, false);
  assert.equal(parsed.edgeTypes.baton_manages, true);
  assert.deepEqual(parsed.hidden.canisters, [TENANT_FE]);
  assert.deepEqual(parsed.hidden.principals, [INSTALLER]);
  assert.deepEqual(parsed.positions[`canister:${TENANT_BE}`], { x: 120, y: 340 });
});

test('parseControlGraphView drops malformed entries instead of throwing', () => {
  const parsed = parseControlGraphView(
    JSON.stringify({
      zoom: 'huge',
      edgeTypes: { ic_controller: 'yes', baton_manages: false },
      hidden: { canisters: ['a', 7, null], principals: 'nope' },
      positions: { good: { x: 1, y: 2 }, bad: { x: 'left', y: 2 }, alsoBad: 5 },
    }),
  );
  assert.equal(parsed.zoom, 1);
  assert.equal(parsed.edgeTypes.ic_controller, true);
  assert.equal(parsed.edgeTypes.baton_manages, false);
  assert.deepEqual(parsed.hidden.canisters, ['a']);
  assert.deepEqual(parsed.hidden.principals, []);
  assert.deepEqual(Object.keys(parsed.positions), ['good']);
});

test('clampControlGraphZoom keeps zoom inside the supported range', () => {
  assert.equal(clampControlGraphZoom(10), MAX_CONTROL_GRAPH_ZOOM);
  assert.equal(clampControlGraphZoom(0.01), MIN_CONTROL_GRAPH_ZOOM);
  assert.equal(clampControlGraphZoom(Number.NaN), 1);
  assert.equal(clampControlGraphZoom(1.2), 1.2);
});

test('standVisibilityKey joins section and stand', () => {
  assert.equal(standVisibilityKey('Deployments', 'testtenant7'), 'Deployments|testtenant7');
});
