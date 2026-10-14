// The conductor's API (text JSON in / out), and an actor that serves the method
// names the pages were written against on top of it. New code should call the
// new methods directly through `conductor`.

const QUERIES = [
  'orchestra_get', 'orchestra_events', 'orchestra_resolve', 'orchestra_export', 'content_status', 'wasm_list',
  'grant_list', 'cycles_get',
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

type Json = Record<string, any>;
type Call = (method: string, args?: Json) => Promise<Json>;

const PERMISSIONS = [
  ['canister.create', 'Create canisters', 'Canisters'], ['canister.deploy', 'Deploy / upgrade canisters', 'Canisters'],
  ['canister.config', 'Configure, rename and tag canisters', 'Canisters'],
  ['canister.lifecycle', 'Start, stop, snapshot, restore', 'Canisters'], ['canister.delete', 'Delete canisters', 'Canisters'],
  ['canister.call', 'Call a granted canister method', 'Canisters'], ['canister.browse', 'Browse canister data', 'Canisters'],
  ['cycles.top_up', 'Top up cycles', 'Canisters'], ['stand.create', 'Create stands', 'Stand'],
  ['stand.edit', 'Rename / edit stands', 'Stand'], ['stand.delete', 'Delete stands', 'Stand'],
  ['section.edit', 'Edit sections and templates', 'Section'], ['grant.assign', 'Appoint commanders', 'Governance'],
  ['wasm.upload', 'Upload files to the store', 'Platform'], ['wasm.authorize', 'Authorize / revoke wasms', 'Platform'],
  ['orchestra.settings', 'Change orchestra settings', 'Platform'], ['alias.manage', 'Manage principal aliases', 'Platform'],
].map(([key, label, group]) => ({ key, label, group }));

function grantView(g: Json): Json {
  const all = g.permissions.includes('*');
  return {
    principal: g.principal, permissions: g.permissions, all_permissions: all, unclaimed: g.principal.startsWith('sha256:'),
    calls: (g.calls || []).map((c: Json) => ({ canister: c.canister_id, method: c.method })),
  };
}

function scopeOf(args: Json): string {
  return args.stand ? `stand:${args.stand}` : args.section ? `section:${args.section}` : 'orchestra';
}

async function tree(call: Call): Promise<Json> {
  const [view, grants] = await Promise.all([call('orchestra_get'), call('grant_list')]);
  const sections: Record<string, Json> = {};
  const scoped = (scope: string) => grants.grants.filter((g: Json) => g.scope === scope).map(grantView);
  for (const c of view.canisters) {
    const sec = (sections[c.section] ??= { name: c.section, description: '', commanders: scoped(`section:${c.section}`), stands: {} });
    const st = (sec.stands[c.stand] ??= {
      name: c.stand, description: '', commanders: scoped(`stand:${c.stand}`), built: !!c.stand_built_at,
      build_error: c.build_error, canisters: [],
    });
    st.canisters.push({ ...c, wasm_key: c.wasm, user_tags: c.tags, url: '', snapshot_id: '' });
  }
  const out: Json[] = Object.values(sections).map((s) => ({ ...s, commander_principal: '', stands: Object.values(s.stands) }));
  const orchestra = scoped('orchestra');
  if (orchestra.length && !out.some((s) => s.name === 'Casals')) out.unshift({ name: 'Casals', commanders: orchestra, stands: [] });
  else out.forEach((s) => s.name === 'Casals' && (s.commanders = orchestra));
  return { sections: out, principal_aliases: view.aliases, scoped: view.scoped };
}

async function metadata(call: Call): Promise<Json> {
  const v = await call('orchestra_get');
  return {
    version: '', orchestra_name: v.name, orchestra_description: v.description, open_access: false,
    wasm_store_canister_id: v.store_id, casals_frontend_canister_id: v.frontend_id,
    monitor_enabled: !!v.monitor?.principal, monitor_principal: v.monitor?.principal ?? '', monitor_service_url: v.monitor?.url ?? '',
    default_min_cycles: v.policy?.min ?? 0, default_topup_cycles: v.policy?.topup ?? 0, treasury_reserve: v.reserve,
    cycles_autopilot: false, cycles_check_interval_secs: 0, display_currency: v.currency, subnet_whitelist: v.subnets,
    canister_type: 'orchestrator', backend_canister_id: v.canister_id, notification_emails: Object.values(v.contacts ?? {}),
  };
}

async function cycles(call: Call, refresh: boolean): Promise<Json> {
  const r = await call(refresh ? 'cycles_refresh' : 'cycles_get');
  const canisters = r.canisters.map((c: Json) => ({
    ...c, kind: 'backend', min_cycles: c.min, topup_cycles: c.topup, refreshed_at: r.balances_at,
    status: !r.balances_at ? 'error' : c.cycles < c.min ? 'low' : 'ok',
  }));
  const count = (s: string) => canisters.filter((c: Json) => c.status === s).length;
  return {
    treasury: { ...r.treasury, autopilot: false, interval_secs: 0 }, canisters, cached_at: r.balances_at,
    totals: { canisters: canisters.length, ok: count('ok'), low: count('low'), critical: 0, frozen: 0, error: count('error') },
  };
}

const ok = (r: Json) => r;

// old method → (call, parsed old args) → old reply object
const LEGACY: Record<string, (call: Call, a: Json) => Promise<Json>> = {
  get_tree: (call) => tree(call),
  casals_metadata: (call) => metadata(call),
  get_settings: (call) => metadata(call),
  get_status: async (call) => {
    const v = await call('orchestra_get');
    return { version: '', orchestra_name: v.name, orchestra_description: v.description, canisters: v.canisters.length };
  },
  get_my_settings: async (call) => {
    const v = await call('orchestra_get');
    return { ok: true, notification_email: v.email, notification_email_status: v.email ? 'pending' : '' };
  },
  set_my_settings: (call, a) => call('me_set', { email: a.notification_email ?? '' }),
  list_sections: async (call) => (await tree(call)).sections.map((s: Json) => ({ ...s, stand_count: s.stands.length })),
  list_authorized_wasms: async (call) =>
    (await call('wasm_list')).wasms.map((w: Json) => ({
      ...w, family: w.key.split('@')[0], version: w.key.split('@')[1] ?? '', wasm_hash: w.sha256, wasm_type: w.type,
      registry_path: w.path, latest: true, section: '', description: '',
    })),
  get_events: async (call, a) =>
    (await call('orchestra_events', { take: a.take ?? 100, ...(a.btype ? { type: a.btype } : {}), ...(a.canister_id ? { canister_id: a.canister_id } : {}) }))
      .events.map((e: Json) => ({ ...e, btype: e.type, kind: e.type, timestamp_secs: e.ts })),
  get_cycles: (call) => cycles(call, true),
  get_cycles_cached: (call) => cycles(call, false),
  refresh_canisters: (call) => cycles(call, true),
  refresh_treasury: (call) => cycles(call, true),
  top_up: (call, a) => call('cycles_top_up', a),
  convert_treasury_icp: (call) => call('cycles_refresh'),
  treasury_send: (call, a) => call('cycles_send', a),
  set_settings: (call, a) =>
    call('orchestra_set', Object.fromEntries(Object.entries({
      name: a.orchestra_name, description: a.orchestra_description, currency: a.display_currency, reserve: a.treasury_reserve,
      ...(a.default_min_cycles !== undefined || a.default_topup_cycles !== undefined
        ? { policy: { min: a.default_min_cycles ?? 0, topup: a.default_topup_cycles ?? 0 } } : {}),
      ...(a.monitor_principal !== undefined ? { monitor: { principal: a.monitor_principal, url: a.monitor_service_url ?? '' } } : {}),
    }).filter(([, v]) => v !== undefined))),
  set_subnet_whitelist: (call, a) => call('orchestra_set', { subnets: a.subnets }),
  list_subnets: async (call) => {
    const v = await call('orchestra_get');
    return { ok: true, subnets: v.subnets.length ? v.subnets : v.creatable_subnets, creatable_subnets: v.creatable_subnets, whitelist_active: !!v.subnets.length };
  },
  create_section: (call, a) => call('section_create', a),
  create_stand: (call, a) => call('stand_create', a),
  rename_section: (call, a) => call('section_set', { section: a.section, name: a.new_name, ...(a.description !== undefined ? { description: a.description } : {}) }),
  rename_stand: (call, a) => call('stand_set', { stand: a.stand, name: a.new_name, ...(a.description !== undefined ? { description: a.description } : {}) }),
  rename_canister: (call, a) => call('canister_set', { canister: a.canister, name: a.new_name }),
  set_canister_tags: (call, a) => call('canister_set', { canister: a.canister, tags: a.tags }),
  delete_section: (call, a) => call('section_delete', a),
  delete_stand: (call, a) => call('stand_delete', a),
  delete_canister: (call, a) => call('canister_delete', a),
  destroy_canister: (call, a) => call('canister_destroy', a),
  destroy_stand: (call, a) => call('stand_destroy', a),
  get_section_arrangement: async (call, a) => {
    const doc = await call('orchestra_export');
    const sec = doc.sections.find((s: Json) => s.name === a.section);
    return { ok: true, section: a.section, arrangements: sec?.template ? { stand_template: sec.template } : null, can_edit: true };
  },
  set_section_arrangement: (call, a) => call('section_set', { section: a.section, template: a.arrangements?.stand_template ?? null }),
  set_commander: (call, a) => call('grant_set', { scope: scopeOf(a), principal: a.commander_principal, permissions: a.permissions ?? ['*'] }),
  set_permissions: (call, a) => call('grant_set', { scope: scopeOf(a), principal: a.commander_principal, permissions: a.permissions }),
  remove_commander: (call, a) => call('grant_remove', { scope: scopeOf(a), principal: a.commander_principal }),
  set_canister_calls: async (call, a) => {
    const g = (await call('grant_list')).grants.find((x: Json) => x.scope === scopeOf(a) && x.principal === a.commander_principal);
    const ids = Object.fromEntries((await call('orchestra_get')).canisters.map((c: Json) => [c.name, c.canister_id]));
    const calls = a.calls.map((c: Json) => ({ canister_id: ids[c.canister] ?? c.canister, method: c.method }));
    return call('grant_set', { scope: scopeOf(a), principal: a.commander_principal, permissions: g?.permissions ?? [], calls });
  },
  call_canister: (call, a) => call('call_canister', a),
  claim_commander: async (call, a) => {
    const r = await call('grant_claim', a);
    return { ...r, claimed: r.claimed.map((s: string) => ({ scope: s.split(':')[0], name: s.split(':')[1] ?? s })) };
  },
  list_permissions: async () => PERMISSIONS,
  list_principal_aliases: async (call) =>
    ({ aliases: Object.entries((await call('orchestra_get')).aliases).map(([principal, name]) => ({ principal, name, description: '' })) }),
  set_principal_alias: (call, a) => call('alias_set', { principal: a.principal, name: a.name }),
  delete_principal_alias: (call, a) => call('alias_set', { principal: a.principal, name: null }),
  register_canister: (call, a) => call('canister_register', a),
  add_authorized_wasm: (call, a) =>
    call('wasm_authorize', { key: a.key, path: `/${a.registry_namespace ?? 'wasm'}/${a.registry_path}`, sha256: a.wasm_hash, type: a.wasm_type ?? '' }),
  remove_authorized_wasm: (call, a) => call('wasm_revoke', a),
  begin_upload: (call) => call('wasm_upload_begin'),
  end_upload: (call, a) => call('wasm_upload_end', { prefix: a.namespace ? `/${a.namespace}/` : '/' }),
  deploy_content: (call, a) => call('content_deploy', a),
  content_deploys: async (call) => ({ ok: true, deploys: (await call('content_status')).deploys }),
  create_canister: (call, a) => call('canister_create', { ...a, wasm: a.wasm_key }),
  upgrade_to: (call, a) => call('canister_upgrade', { ...a, wasm: a.wasm_key }),
  propose_upgrade: async (call, a) => {
    const c = (await call('orchestra_get')).canisters.find((x: Json) => x.name === (a.canister ?? a.targets?.[0]?.canister));
    const targets = (a.targets ?? [a]).map((t: Json) => ({ canister: t.canister, wasm: t.wasm_key }));
    return call('baton_propose_upgrade', { stand: c?.stand, targets });
  },
  propose_assets: async (call, a) => {
    const c = (await call('orchestra_get')).canisters.find((x: Json) => x.name === (a.canister ?? a.targets?.[0]?.canister));
    return call('baton_propose_content', { stand: c?.stand, targets: a.targets ?? [a] });
  },
  create_snapshot: (call, a) => call('canister_snapshot', a),
  revert_snapshot: (call, a) => call('canister_restore', a),
  stop_canister: (call, a) => call('canister_stop', a),
  start_canister: (call, a) => call('canister_start', a),
  set_canister_controllers: (call, a) => call('canister_controllers', a),
  canister_browse: async (call, a) => call('canister_browse', a).then(ok),
};

/** An actor answering the old method names from the new conductor API. */
export function legacyActor(actor: any): any {
  const call: Call = async (method, args = {}) => {
    const raw: string = await actor[method](JSON.stringify(args));
    const reply = JSON.parse(raw);
    if (reply.ok === false) throw new Error(reply.error || `${method} failed`);
    return reply;
  };
  return new Proxy(actor, {
    get(target, name: string) {
      if (name in target || typeof name !== 'string') return target[name];
      const legacy = LEGACY[name];
      return async (arg?: string) => {
        if (!legacy) return JSON.stringify({ ok: false, error: `${name} is not available on this conductor` });
        try {
          return JSON.stringify(await legacy(call, arg ? JSON.parse(arg) : {}));
        } catch (e) {
          return JSON.stringify({ ok: false, error: (e as Error).message });
        }
      };
    },
  });
}
