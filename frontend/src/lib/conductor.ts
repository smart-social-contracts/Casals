// The conductor's Candid interface: every method takes and returns JSON text.

const QUERIES = [
  'orchestra_get', 'orchestra_events', 'orchestra_resolve', 'orchestra_export', 'content_status', 'wasm_list',
  'grant_list', 'cycles_get', 'subnet_list',
];
const UPDATES = [
  'orchestra_up', 'orchestra_set', 'alias_set', 'me_set', 'section_create', 'section_set', 'section_delete',
  'stand_create', 'stand_set', 'stand_delete', 'stand_destroy', 'canister_create', 'canister_register',
  'canister_set', 'canister_upgrade', 'canister_controllers', 'canister_start', 'canister_stop',
  'canister_snapshot', 'canister_restore', 'canister_delete', 'canister_destroy', 'canister_browse',
  'content_deploy', 'wasm_upload_begin', 'wasm_upload_end', 'wasm_authorize', 'wasm_revoke', 'grant_set',
  'grant_remove', 'grant_claim', 'call_canister', 'baton_propose_upgrade', 'baton_propose_content',
  'cycles_refresh', 'cycles_top_up', 'cycles_send',
];

export const idlFactory = ({ IDL }: { IDL: any }) =>
  IDL.Service(Object.fromEntries([
    ...QUERIES.map((m) => [m, IDL.Func([IDL.Text], [IDL.Text], ['query'])]),
    ...UPDATES.map((m) => [m, IDL.Func([IDL.Text], [IDL.Text], [])]),
  ]));
