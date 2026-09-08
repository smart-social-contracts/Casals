import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Tree } from './api.ts';
import {
  buildControlGraph,
  DEFAULT_CONTROL_GRAPH_LAYERS,
  layoutControlGraph,
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
  assert.ok(graph.edges.some((e) => e.type === 'baton_ic_control' && e.to === `canister:${REALM_BE}`));
});

test('buildControlGraph respects layer toggles', () => {
  const graph = buildControlGraph(fixtureTree(), null, [], {
    layers: { icControllers: false, commanders: true, baton: false },
  });
  assert.equal(graph.edges.every((e) => e.type === 'casals_commander'), true);
});

test('layoutControlGraph assigns positions by rank', () => {
  const graph = buildControlGraph(fixtureTree(), null, [], { casalsBackendId: CASALS });
  const positions = layoutControlGraph(graph, 900);
  const multisig = positions.get(`canister:${MULTISIG}`);
  const frontend = positions.get(`canister:${REALM_FE}`);
  assert.ok(multisig && frontend);
  assert.ok(multisig.y < frontend.y);
});
