import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Tree } from './api.ts';
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
const REALM_BE = 'realm-backend-principal';
const REALM_FE = 'realm-frontend-principal';
const INSTALLER = 'installer-principal';

function fixtureTree(): Tree {
  return {
    sections: [
      {
        name: 'Casals',
        description: '',
        commander_principal: '',
        stands: [
          {
            name: 'System',
            description: '',
            commander_principal: '',
            canisters: [
              {
                name: 'multisig',
                canister_id: MULTISIG,
                kind: 'backend',
                wasm_key: 'orchestration-multisig',
                wasm_hash: '',
                status: 'installed',
                url: '',
                snapshot_id: '',
                controllers: [MULTISIG],
              },
              {
                name: 'casals-backend',
                canister_id: CASALS,
                kind: 'backend',
                wasm_key: 'casals-backend',
                wasm_hash: '',
                status: 'installed',
                url: '',
                snapshot_id: '',
                controllers: [MULTISIG, CASALS],
              },
            ],
          },
        ],
      },
      {
        name: 'Deployments',
        description: '',
        commander_principal: INSTALLER,
        commanders: [{ principal: INSTALLER, all_permissions: true }],
        stands: [
          {
            name: 'testrealm7',
            description: '',
            commander_principal: REALM_BE,
            commanders: [{ principal: REALM_BE, all_permissions: true }],
            canisters: [
              {
                name: 'testrealm7-baton',
                canister_id: BATON,
                kind: 'backend',
                wasm_key: 'orchestration-baton',
                wasm_hash: '',
                status: 'installed',
                url: '',
                snapshot_id: '',
                controllers: [MULTISIG, CASALS, BATON],
              },
              {
                name: 'testrealm7-backend',
                canister_id: REALM_BE,
                kind: 'backend',
                wasm_key: 'realm-backend',
                wasm_hash: '',
                status: 'installed',
                url: '',
                snapshot_id: '',
                controllers: [MULTISIG, CASALS, BATON],
              },
              {
                name: 'testrealm7-frontend',
                canister_id: REALM_FE,
                kind: 'frontend',
                wasm_key: 'realm-assets',
                wasm_hash: '',
                status: 'installed',
                url: '',
                snapshot_id: '',
                controllers: [MULTISIG, CASALS, BATON],
              },
            ],
          },
        ],
      },
    ],
  };
}

test('buildControlGraph emits IC controller and commander edges', () => {
  const graph = buildControlGraph(fixtureTree(), null, [], {
    layers: DEFAULT_CONTROL_GRAPH_LAYERS,
    casalsBackendId: CASALS,
  });

  assert.ok(graph.edges.some((e) => e.type === 'ic_controller' && e.to === `canister:${REALM_BE}`));
  assert.ok(
    graph.edges.some(
      (e) =>
        e.type === 'casals_commander' &&
        e.from === `principal:${INSTALLER}` &&
        e.to === `canister:${REALM_BE}`,
    ),
  );
});

test('buildControlGraph omits uphill IC edges between orchestra tiers', () => {
  const tree = fixtureTree();
  const multisig = tree.sections[0].stands[0].canisters.find((c) => c.name === 'multisig');
  assert.ok(multisig);
  multisig.controllers = [MULTISIG, CASALS];

  const graph = buildControlGraph(tree, null, [], { casalsBackendId: CASALS });

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
    canister: { name: 'multisig', canister_id: 'multisig', kind: 'backend', wasm_key: 'orchestration-multisig' },
  };
  assert.equal(isUphillOrchestraIcEdge(from, to), true);
  assert.equal(isUphillOrchestraIcEdge(to, from), false);
});

test('inferManagedCanistersFromTree reads baton id from cached controllers', () => {
  const tree = fixtureTree();
  const inferred = inferManagedCanistersFromTree(tree, BATON, {
    section: 'Deployments',
    stand: 'testrealm7',
  });
  assert.deepEqual(inferred.sort(), [REALM_BE, REALM_FE].sort());
});

test('inferManagedCanistersFromStand links same-stand peers without controller cache', () => {
  const tree = fixtureTree();
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        c.controllers = [];
      }
    }
  }
  const inferred = inferManagedCanistersFromStand(tree, BATON, {
    section: 'Deployments',
    stand: 'testrealm7',
  });
  assert.deepEqual(inferred.sort(), [REALM_BE, REALM_FE].sort());
});

test('buildControlGraph infers baton_manages without orchestration status', () => {
  const tree = fixtureTree();
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        c.controllers = [];
      }
    }
  }
  const graph = buildControlGraph(
    tree,
    null,
    [{ name: 'testrealm7-baton', canister_id: BATON, section: 'Deployments', stand: 'testrealm7' }],
    { casalsBackendId: CASALS },
  );
  assert.ok(graph.edges.some((e) => e.type === 'baton_manages' && e.to === `canister:${REALM_BE}`));
  assert.ok(graph.edges.some((e) => e.type === 'baton_manages' && e.to === `canister:${REALM_FE}`));
});

test('buildControlGraph adds baton edges from orchestration status', () => {
  const graph = buildControlGraph(
    fixtureTree(),
    {
      ok: true,
      batons: [
        {
          name: 'testrealm7-baton',
          canister_id: BATON,
          config: { top_commander: CASALS },
          commanders: [{ principal: CASALS }, { principal: REALM_BE }],
          managed_canisters: [REALM_BE, REALM_FE],
        },
      ],
    },
    [{ name: 'testrealm7-baton', canister_id: BATON, managed_canisters: [REALM_BE, REALM_FE] }],
    { casalsBackendId: CASALS },
  );

  assert.ok(graph.edges.some((e) => e.type === 'baton_top_commander' && e.to === `canister:${BATON}`));
  assert.ok(graph.edges.some((e) => e.type === 'baton_manages' && e.to === `canister:${REALM_BE}`));
  assert.ok(
    graph.edges.some(
      (e) => e.type === 'ic_controller' && e.from === `canister:${BATON}` && e.to === `canister:${REALM_BE}`,
    ),
  );
});

test('buildControlGraph respects layer toggles', () => {
  const full = buildControlGraph(fixtureTree(), null, [], {
    layers: DEFAULT_CONTROL_GRAPH_LAYERS,
  });
  const graph = filterControlGraphByEdgeTypes(full, {
    ic_controller: false,
    casals_commander: true,
    baton_top_commander: false,
    baton_commander: false,
    baton_manages: false,
  });
  assert.equal(graph.edges.every((e) => e.type === 'casals_commander'), true);
});

test('filterControlGraphByEdgeTypes can hide a single edge type', () => {
  const full = buildControlGraph(
    fixtureTree(),
    {
      ok: true,
      batons: [
        {
          name: 'testrealm7-baton',
          canister_id: BATON,
          config: { top_commander: CASALS },
          managed_canisters: [REALM_BE, REALM_FE],
        },
      ],
    },
    [{ name: 'testrealm7-baton', canister_id: BATON, managed_canisters: [REALM_BE, REALM_FE] }],
    { casalsBackendId: CASALS },
  );
  const graph = filterControlGraphByEdgeTypes(full, {
    ic_controller: true,
    casals_commander: true,
    baton_top_commander: true,
    baton_commander: true,
    baton_manages: false,
  });
  assert.ok(full.edges.some((e) => e.type === 'baton_manages'));
  assert.ok(!graph.edges.some((e) => e.type === 'baton_manages'));
});

test('layoutControlGraph assigns positions by rank', () => {
  const graph = buildControlGraph(fixtureTree(), null, [], { casalsBackendId: CASALS });
  const positions = layoutControlGraph(graph, 900);
  const multisig = positions.get(`canister:${MULTISIG}`);
  const frontend = positions.get(`canister:${REALM_FE}`);
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
  const full = buildControlGraph(fixtureTree(), null, [], { casalsBackendId: CASALS });
  const filtered = filterControlGraph(full, {
    hiddenSections: new Set(['Deployments']),
    hiddenStands: new Set(),
    hiddenCanisters: new Set(),
    hiddenPrincipals: new Set(),
  });
  assert.ok(!filtered.nodes.some((n) => n.canister?.canister_id === REALM_BE));
  assert.ok(filtered.nodes.some((n) => n.canister?.canister_id === CASALS));
});

test('filterControlGraph hides a single canister', () => {
  const full = buildControlGraph(fixtureTree(), null, [], { casalsBackendId: CASALS });
  const filtered = filterControlGraph(full, {
    hiddenSections: new Set(),
    hiddenStands: new Set(),
    hiddenCanisters: new Set([REALM_FE]),
    hiddenPrincipals: new Set(),
  });
  assert.ok(!filtered.nodes.some((n) => n.canister?.canister_id === REALM_FE));
});

test('filterControlGraph hides a principal node', () => {
  const full = buildControlGraph(fixtureTree(), null, [], { casalsBackendId: CASALS });
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
    hiddenStands: new Set(['Deployments|testrealm7']),
    hiddenCanisters: new Set([REALM_FE]),
    hiddenPrincipals: new Set([INSTALLER]),
    positions: { [`canister:${REALM_BE}`]: { x: 120, y: 340 } },
  });

  const parsed = parseControlGraphView(JSON.stringify(view));
  assert.equal(parsed.name, 'baton only');
  assert.equal(parsed.zoom, 1.4);
  assert.equal(parsed.edgeTypes.ic_controller, false);
  assert.equal(parsed.edgeTypes.baton_manages, true);
  assert.deepEqual(parsed.hidden.canisters, [REALM_FE]);
  assert.deepEqual(parsed.hidden.principals, [INSTALLER]);
  assert.deepEqual(parsed.positions[`canister:${REALM_BE}`], { x: 120, y: 340 });
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
  assert.equal(standVisibilityKey('Deployments', 'testrealm7'), 'Deployments|testrealm7');
});
