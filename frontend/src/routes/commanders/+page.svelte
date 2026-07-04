<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import {
    getTree, setCommander, setPermissions, listPermissions, backendCanisterId,
    getOrchestrationPolicies, setOrchestrationPolicies, listGovernanceRequests,
    approveGovernanceRequest, rejectGovernanceRequest, listOrchestrationActions,
    type Tree, type Permission, type ApprovalPolicy, type GovernanceRequest,
  } from '$lib/api';
  import { identity, isAuthenticated } from '$lib/auth';
  import { listCanisterControllers } from '$lib/controllerAccess';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  interface CommanderRow {
    scope: 'section' | 'stand' | 'controller';
    section: string;
    stand?: string;
    principal: string;
    label: string;            // hierarchy path label
    permissions: string[];    // resolved granted keys
    allPermissions: boolean;  // true => full access ("*")
  }

  let tree = $state<Tree | null>(null);
  let catalog = $state<Permission[]>([]);
  let controllerPrincipals = $state<string[]>([]);
  let loading = $state(true);
  let error = $state('');
  let filterQuery = $state('');

  async function loadControllers() {
    const canisterId = backendCanisterId();
    const id = get(identity);
    if (!canisterId || !id) {
      controllerPrincipals = [];
      return;
    }
    try {
      controllerPrincipals = await listCanisterControllers(canisterId, id);
    } catch {
      controllerPrincipals = [];
    }
  }

  async function load() {
    loading = true;
    error = '';
    try {
      const [t, perms] = await Promise.all([
        getTree(),
        listPermissions().catch(() => []),
      ]);
      tree = t;
      if (perms.length) catalog = perms;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load data';
    } finally {
      loading = false;
    }
    void loadControllers();
  }

  onMount(() => {
    void load();
    void loadGovernanceRequests();
    return identity.subscribe((id) => {
      if (id && !loading) void loadControllers();
    });
  });

  // Catalog grouped by group, in declaration order.
  const groupedCatalog = $derived.by(() => {
    const groups: { name: string; perms: Permission[] }[] = [];
    for (const p of catalog) {
      let g = groups.find((x) => x.name === p.group);
      if (!g) { g = { name: p.group, perms: [] }; groups.push(g); }
      g.perms.push(p);
    }
    return groups;
  });

  const labelFor = (key: string) => catalog.find((p) => p.key === key)?.label ?? key;

  // Flatten tree + Casals backend controllers into commander rows.
  const rows = $derived.by((): CommanderRow[] => {
    const out: CommanderRow[] = [];
    for (const principal of controllerPrincipals) {
      out.push({
        scope: 'controller',
        section: '',
        principal,
        label: 'Casals backend',
        permissions: [],
        allPermissions: true,
      });
    }
    if (!tree) return out;
    for (const sec of tree.sections) {
      if (sec.commander_principal) {
        out.push({
          scope: 'section', section: sec.name, principal: sec.commander_principal,
          label: sec.name, permissions: sec.permissions ?? [], allPermissions: sec.all_permissions ?? true,
        });
      }
      for (const dk of sec.stands) {
        if (dk.commander_principal) {
          out.push({
            scope: 'stand', section: sec.name, stand: dk.name, principal: dk.commander_principal,
            label: `${sec.name} / ${dk.name}`, permissions: dk.permissions ?? [], allPermissions: dk.all_permissions ?? true,
          });
        }
      }
    }
    return out;
  });

  const filteredRows = $derived.by(() => {
    const q = filterQuery.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      [r.principal, r.label, r.section, r.stand ?? '', r.scope, ...r.permissions]
        .some((v) => v.toLowerCase().includes(q))
    );
  });

  // Group by principal so we can see all roles for each person.
  const byPrincipal = $derived.by(() => {
    const map = new Map<string, CommanderRow[]>();
    for (const r of filteredRows) {
      if (!map.has(r.principal)) map.set(r.principal, []);
      map.get(r.principal)!.push(r);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  });

  async function copyToClipboard(text: string) {
    if (await copyText(text)) toasts.success('Copied');
    else toasts.error('Copy failed');
  }

  // ── Assign commander modal ──────────────────────────────────────────────────
  const sectionOptions = $derived((tree?.sections ?? []).map((s) => s.name));
  function standNames(sectionName: string) {
    return (tree?.sections.find((s) => s.name === sectionName)?.stands ?? []).map((d) => d.name);
  }

  let busy = $state(false);

  let assignOpen = $state(false);
  let assignScope = $state<'section' | 'stand'>('section');
  let assignSection = $state('');
  let assignStand = $state('');
  let assignPrincipal = $state('');
  let assignPerms = $state<Set<string>>(new Set());

  function openAssign() {
    assignScope = 'section';
    assignSection = sectionOptions[0] ?? '';
    assignStand = '';
    assignPrincipal = '';
    // Default a new commander to full access (all permissions checked).
    assignPerms = new Set(catalog.map((p) => p.key));
    assignOpen = true;
  }

  const assignAllChecked = $derived(catalog.length > 0 && assignPerms.size >= catalog.length);
  function assignToggleAll() {
    assignPerms = assignAllChecked ? new Set() : new Set(catalog.map((p) => p.key));
  }
  function assignTogglePerm(key: string) {
    const next = new Set(assignPerms);
    if (next.has(key)) next.delete(key); else next.add(key);
    assignPerms = next;
  }

  async function submitAssign() {
    if (!assignPrincipal.trim()) return;
    busy = true;
    try {
      const target = assignScope === 'stand' && assignStand
        ? { stand: assignStand }
        : { section: assignSection };
      const permissions: string[] | '*' = assignAllChecked ? '*' : [...assignPerms];
      await setCommander({ ...target, commander_principal: assignPrincipal.trim(), permissions });
      toasts.success('Commander assigned');
      assignOpen = false;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }

  // ── Permissions editor modal ────────────────────────────────────────────────
  let permsOpen = $state(false);
  let permsRow = $state<CommanderRow | null>(null);
  let permsSelected = $state<Set<string>>(new Set());

  function openPerms(row: CommanderRow) {
    permsRow = row;
    // If full access, pre-check everything; otherwise the explicit subset.
    permsSelected = new Set(row.allPermissions ? catalog.map((p) => p.key) : row.permissions);
    permsOpen = true;
  }

  function togglePerm(key: string) {
    const next = new Set(permsSelected);
    if (next.has(key)) next.delete(key); else next.add(key);
    permsSelected = next;
  }

  const allChecked = $derived(catalog.length > 0 && permsSelected.size >= catalog.length);
  function toggleAll() {
    permsSelected = allChecked ? new Set() : new Set(catalog.map((p) => p.key));
  }

  async function submitPerms() {
    if (!permsRow) return;
    busy = true;
    try {
      const target = permsRow.scope === 'stand' ? { stand: permsRow.stand! } : { section: permsRow.section };
      // Collapse a full set to "*" so it reads as full access.
      const permissions: string[] | '*' = allChecked ? '*' : [...permsSelected];
      await setPermissions({ ...target, permissions });
      toasts.success('Permissions updated');
      permsOpen = false;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }

  // ── Orchestration approval policies (per section) ─────────────────────────
  let policiesOpen = $state(false);
  let policiesSection = $state('');
  let policiesLabels = $state<Record<string, string>>({});
  let policiesDraft = $state<Record<string, ApprovalPolicy>>({});
  let orchestrationActions = $state<Permission[]>([]);

  async function openPolicies(sectionName: string) {
    policiesSection = sectionName;
    busy = true;
    try {
      if (!orchestrationActions.length) {
        orchestrationActions = await listOrchestrationActions().catch(() => []);
      }
      const snap = await getOrchestrationPolicies(sectionName);
      policiesLabels = snap.labels ?? {};
      policiesDraft = { ...(snap.policies ?? {}) };
      for (const a of orchestrationActions) {
        policiesDraft[a.key] ??= { threshold: 1, eligible: [], required: [] };
      }
      policiesOpen = true;
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to load policies');
    } finally {
      busy = false;
    }
  }

  function policyEligibleText(action: string): string {
    return (policiesDraft[action]?.eligible ?? []).join('\n');
  }

  function policyRequiredText(action: string): string {
    return (policiesDraft[action]?.required ?? []).join('\n');
  }

  function setPolicyEligible(action: string, text: string) {
    const eligible = text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    policiesDraft = {
      ...policiesDraft,
      [action]: { ...policiesDraft[action], eligible },
    };
  }

  function setPolicyRequired(action: string, text: string) {
    const required = text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    policiesDraft = {
      ...policiesDraft,
      [action]: { ...policiesDraft[action], required },
    };
  }

  async function submitPolicies() {
    if (!policiesSection) return;
    busy = true;
    try {
      await setOrchestrationPolicies({ section: policiesSection, policies: policiesDraft });
      toasts.success('Orchestration policies saved');
      policiesOpen = false;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }

  // ── Pending governance requests ───────────────────────────────────────────
  let governanceRequests = $state<GovernanceRequest[]>([]);

  async function loadGovernanceRequests() {
    try {
      const res = await listGovernanceRequests({ status: 'PENDING' });
      governanceRequests = res.requests ?? [];
    } catch {
      governanceRequests = [];
    }
  }

  async function approveRequest(requestId: string) {
    busy = true;
    try {
      await approveGovernanceRequest(requestId);
      toasts.success('Approved');
      await loadGovernanceRequests();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }

  async function rejectRequest(requestId: string) {
    busy = true;
    try {
      await rejectGovernanceRequest(requestId);
      toasts.success('Rejected');
      await loadGovernanceRequests();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head><title>Casals · Commanders</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <!-- Header -->
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Commanders</h1>
      <p class="text-sm text-primary-500 mt-1">Section and stand commanders, Casals controllers, and their permissions</p>
    </div>
    <div class="flex items-center gap-2 self-start">
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={openAssign}>
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          Assign
        </button>
      {/if}
      <button class="btn-secondary btn-sm" onclick={load}>
        <svg class="w-4 h-4 {loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
        </svg>
        Refresh
      </button>
    </div>
  </div>

  <!-- Filter -->
  {#if !loading && rows.length > 0}
    <div class="relative">
      <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-primary-400 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
      </svg>
      <input
        type="text"
        class="input pl-9 {filterQuery ? 'pr-9' : ''} text-sm"
        placeholder="Filter by principal, section, stand, permission…"
        bind:value={filterQuery}
      />
      {#if filterQuery}
        <button type="button" class="absolute right-3 top-1/2 -translate-y-1/2 text-primary-400 hover:text-primary-600" aria-label="Clear" onclick={() => (filterQuery = '')}>
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      {/if}
    </div>
  {/if}

  <!-- Error -->
  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
  {/if}

  <!-- Loading -->
  {#if loading}
    <div class="space-y-3">
      {#each [1, 2, 3] as n (n)}
        <div class="card p-4 flex items-center gap-4">
          <div class="skeleton h-10 w-10 rounded-full shrink-0"></div>
          <div class="flex-1 space-y-2"><div class="skeleton h-4 w-64"></div><div class="skeleton h-3 w-40"></div></div>
        </div>
      {/each}
    </div>

  {:else if rows.length === 0}
    <div class="text-center py-16">
      <svg class="w-12 h-12 mx-auto text-primary-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
      </svg>
      <p class="text-primary-500 text-sm font-medium">No commanders assigned yet</p>
      {#if $isAuthenticated}
        <p class="text-primary-400 text-xs mt-1">Use the Assign button or set commanders from the Orchestra tab.</p>
      {/if}
    </div>

  {:else if filterQuery && byPrincipal.length === 0}
    <div class="text-center py-10 text-primary-400 text-sm">No results for <strong class="text-primary-700">"{filterQuery}"</strong></div>

  {:else}
    <div class="space-y-3">
      {#each byPrincipal as [principal, pRows] (principal)}
        <div class="card overflow-hidden">
          <!-- Principal header -->
          <div class="flex items-center gap-3 px-4 py-3 bg-primary-50/60 border-b border-primary-100">
            <div class="w-9 h-9 rounded-full bg-primary-100 flex items-center justify-center shrink-0">
              <svg class="w-5 h-5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
              </svg>
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 min-w-0">
                <div class="font-mono text-sm text-primary-800 truncate" title={principal}>{principal}</div>
                {#if pRows.some((r) => r.scope === 'controller')}
                  <span class="badge shrink-0 bg-amber-50 text-amber-800 border border-amber-200">controller</span>
                {/if}
              </div>
              <div class="text-xs text-primary-400 mt-0.5">{pRows.length} role{pRows.length !== 1 ? 's' : ''}</div>
            </div>
            <button class="icon-btn shrink-0" aria-label="Copy principal" onclick={() => copyToClipboard(principal)}>
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 0 1 1.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.185a9.064 9.064 0 0 0-1.5.124" />
              </svg>
            </button>
          </div>

          <!-- Roles list -->
          <div class="divide-y divide-primary-50">
            {#each pRows as row (`${row.scope}:${row.label}:${row.section}:${row.stand ?? ''}`)}
              <div class="px-4 py-3 pl-6">
                <div class="flex items-center gap-3">
                  <span class="badge shrink-0 {row.scope === 'controller'
                    ? 'bg-amber-50 text-amber-800 border border-amber-200'
                    : row.scope === 'section' ? 'badge-primary' : 'badge-neutral'}">{row.scope}</span>
                  <span class="text-sm text-primary-800 flex-1 truncate">
                    {#if row.scope === 'controller'}
                      <span class="font-medium">{row.label}</span>
                      <span class="text-primary-400 ml-1">· full Casals admin</span>
                    {:else if row.stand}
                      <span class="text-primary-500">{row.section}</span><span class="text-primary-300 mx-1">/</span><span class="font-medium">{row.stand}</span>
                    {:else}
                      <span class="font-medium">{row.section}</span>
                    {/if}
                  </span>
                  {#if $isAuthenticated && row.scope !== 'controller'}
                    <button class="btn-ghost btn-sm text-xs shrink-0" onclick={() => openPerms(row)}>Permissions</button>
                  {/if}
                </div>

                <!-- Permission chips -->
                <div class="mt-2 flex flex-wrap items-center gap-1.5 pl-1">
                  {#if row.allPermissions}
                    <span class="badge badge-primary text-[11px]">Full access</span>
                  {:else if row.permissions.length === 0}
                    <span class="text-xs text-amber-600">No permissions — cannot act</span>
                  {:else}
                    {#each row.permissions as key (key)}
                      <span class="inline-flex items-center rounded bg-primary-50 border border-primary-100 px-2 py-0.5 text-[11px] text-primary-600">{labelFor(key)}</span>
                    {/each}
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/each}
    </div>

    <p class="text-xs text-primary-400 text-right">
      {byPrincipal.length} principal{byPrincipal.length !== 1 ? 's' : ''} ·
      {rows.length} role{rows.length !== 1 ? 's' : ''}
      {#if filterQuery}(filtered){/if}
    </p>
  {/if}

  {#if governanceRequests.length > 0}
    <div class="card p-4 space-y-3">
      <h2 class="text-sm font-semibold text-primary-900">Pending orchestration approvals</h2>
      <div class="space-y-2">
        {#each governanceRequests as req (req.request_id)}
          <div class="border border-primary-100 rounded-lg p-3 flex flex-col sm:flex-row sm:items-center gap-3">
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium text-primary-900">{req.action_label ?? req.action}</div>
              <div class="text-xs text-primary-500 mt-0.5">
                {req.section_name} · {req.approval_count ?? req.approvals.length}/{req.threshold ?? 1} approvals
                {#if req.missing_required?.length}
                  · required: {req.missing_required.length} missing
                {/if}
              </div>
            </div>
            {#if $isAuthenticated}
              <div class="flex gap-2 shrink-0">
                <button class="btn-primary btn-sm" disabled={busy} onclick={() => approveRequest(req.request_id)}>Approve</button>
                <button class="btn-ghost btn-sm" disabled={busy} onclick={() => rejectRequest(req.request_id)}>Reject</button>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if tree?.sections?.length}
    <div class="card p-4 space-y-3">
      <h2 class="text-sm font-semibold text-primary-900">Orchestration approval policies</h2>
      <p class="text-xs text-primary-500">N-of-M rules per sensitive action (create/upgrade baton, multisig, hand-off, pipeline).</p>
      <div class="flex flex-wrap gap-2">
        {#each tree.sections as sec (sec.name)}
          {#if $isAuthenticated}
            <button class="btn-secondary btn-sm" disabled={busy} onclick={() => openPolicies(sec.name)}>
              {sec.name} policies
            </button>
          {/if}
        {/each}
      </div>
    </div>
  {/if}
</div>

<!-- Assign commander modal -->
{#if assignOpen}
  <div class="fixed inset-0 z-40 flex items-center justify-center">
    <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={() => (assignOpen = false)}></button>
    <div class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 space-y-4 max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-semibold text-primary-900">Assign commander</h3>
      <div>
        <span class="label">Scope</span>
        <div class="flex gap-2 mt-1">
          <button class="btn-sm {assignScope === 'section' ? 'btn-primary' : 'btn-secondary'}" onclick={() => { assignScope = 'section'; assignStand = ''; }}>Section</button>
          <button class="btn-sm {assignScope === 'stand' ? 'btn-primary' : 'btn-secondary'}" onclick={() => (assignScope = 'stand')}>Stand</button>
        </div>
      </div>
      <div>
        <label class="label" for="assign-section">Section</label>
        <select id="assign-section" class="input" bind:value={assignSection}>
          {#each sectionOptions as name (name)}<option value={name}>{name}</option>{/each}
        </select>
      </div>
      {#if assignScope === 'stand'}
        <div>
          <label class="label" for="assign-stand">Stand</label>
          <select id="assign-stand" class="input" bind:value={assignStand}>
            <option value="">— select a stand —</option>
            {#each standNames(assignSection) as name (name)}<option value={name}>{name}</option>{/each}
          </select>
        </div>
      {/if}
      <div>
        <label class="label" for="assign-principal">Principal</label>
        <input id="assign-principal" type="text" class="input font-mono text-sm" placeholder="aaaaa-aa…" bind:value={assignPrincipal} />
      </div>

      <!-- Permissions -->
      <div class="border-t border-primary-100 pt-3 space-y-3">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="w-4 h-4 rounded border-primary-300" checked={assignAllChecked} onchange={assignToggleAll} />
          <span class="text-sm font-semibold text-primary-800">Full access (all permissions)</span>
        </label>
        {#each groupedCatalog as group (group.name)}
          <div>
            <div class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider mb-1.5">{group.name}</div>
            <div class="grid sm:grid-cols-2 gap-1.5">
              {#each group.perms as perm (perm.key)}
                <label class="flex items-center gap-2 cursor-pointer rounded px-2 py-1.5 hover:bg-primary-50">
                  <input type="checkbox" class="w-4 h-4 rounded border-primary-300" checked={assignPerms.has(perm.key)} onchange={() => assignTogglePerm(perm.key)} />
                  <span class="text-sm text-primary-700">{perm.label}</span>
                </label>
              {/each}
            </div>
          </div>
        {/each}
      </div>

      <div class="flex items-center justify-between pt-1 border-t border-primary-100">
        <span class="text-xs text-primary-400">{assignPerms.size} of {catalog.length} permissions</span>
        <div class="flex gap-3">
          <button class="btn-secondary btn-sm" onclick={() => (assignOpen = false)} disabled={busy}>Cancel</button>
          <button class="btn-primary btn-sm" disabled={busy || !assignPrincipal.trim() || (assignScope === 'stand' && !assignStand)} onclick={submitAssign}>
            {busy ? 'Assigning…' : 'Assign'}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- Permissions editor modal -->
{#if permsOpen && permsRow}
  <div class="fixed inset-0 z-40 flex items-center justify-center">
    <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={() => (permsOpen = false)}></button>
    <div class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 space-y-4 max-h-[90vh] overflow-y-auto">
      <div>
        <h3 class="text-lg font-semibold text-primary-900">Permissions</h3>
        <p class="text-sm text-primary-500 mt-0.5">
          {permsRow.scope === 'stand' ? `Stand "${permsRow.stand}"` : `Section "${permsRow.section}"`} ·
          <span class="font-mono">{permsRow.principal.slice(0, 12)}…</span>
        </p>
      </div>

      <label class="flex items-center gap-2 cursor-pointer border-b border-primary-100 pb-3">
        <input type="checkbox" class="w-4 h-4 rounded border-primary-300" checked={allChecked} onchange={toggleAll} />
        <span class="text-sm font-semibold text-primary-800">Full access (all permissions)</span>
      </label>

      {#each groupedCatalog as group (group.name)}
        <div>
          <div class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider mb-1.5">{group.name}</div>
          <div class="grid sm:grid-cols-2 gap-1.5">
            {#each group.perms as perm (perm.key)}
              <label class="flex items-center gap-2 cursor-pointer rounded px-2 py-1.5 hover:bg-primary-50">
                <input type="checkbox" class="w-4 h-4 rounded border-primary-300" checked={permsSelected.has(perm.key)} onchange={() => togglePerm(perm.key)} />
                <span class="text-sm text-primary-700">{perm.label}</span>
              </label>
            {/each}
          </div>
        </div>
      {/each}

      <div class="flex items-center justify-between pt-2 border-t border-primary-100">
        <span class="text-xs text-primary-400">{permsSelected.size} of {catalog.length} selected</span>
        <div class="flex gap-3">
          <button class="btn-secondary btn-sm" onclick={() => (permsOpen = false)} disabled={busy}>Cancel</button>
          <button class="btn-primary btn-sm" disabled={busy} onclick={submitPerms}>{busy ? 'Saving…' : 'Save permissions'}</button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- Orchestration policies modal -->
{#if policiesOpen}
  <div class="fixed inset-0 z-40 flex items-center justify-center">
    <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={() => (policiesOpen = false)}></button>
    <div class="relative bg-white rounded-xl shadow-xl max-w-2xl w-full mx-4 p-6 space-y-4 max-h-[90vh] overflow-y-auto">
      <div>
        <h3 class="text-lg font-semibold text-primary-900">Orchestration policies</h3>
        <p class="text-sm text-primary-500 mt-0.5">Section <strong>{policiesSection}</strong> · threshold / eligible / required approvers per action</p>
      </div>
      {#each orchestrationActions as action (action.key)}
        {@const pol = policiesDraft[action.key] ?? { threshold: 1, eligible: [], required: [] }}
        <div class="border border-primary-100 rounded-lg p-3 space-y-2">
          <div class="text-sm font-medium text-primary-900">{action.label}</div>
          <label class="block text-xs text-primary-500">
            Threshold (M of N)
            <input
              type="number"
              min="1"
              class="input mt-1"
              value={pol.threshold}
              oninput={(e) => {
                const threshold = Math.max(1, parseInt(e.currentTarget.value, 10) || 1);
                policiesDraft = { ...policiesDraft, [action.key]: { ...pol, threshold } };
              }}
            />
          </label>
          <label class="block text-xs text-primary-500">
            Eligible approvers (one principal per line; empty = any commander with permission)
            <textarea class="input mt-1 font-mono text-xs min-h-[4rem]" value={policyEligibleText(action.key)} oninput={(e) => setPolicyEligible(action.key, e.currentTarget.value)}></textarea>
          </label>
          <label class="block text-xs text-primary-500">
            Required signers (must approve)
            <textarea class="input mt-1 font-mono text-xs min-h-[3rem]" value={policyRequiredText(action.key)} oninput={(e) => setPolicyRequired(action.key, e.currentTarget.value)}></textarea>
          </label>
        </div>
      {/each}
      <div class="flex justify-end gap-3 pt-2 border-t border-primary-100">
        <button class="btn-secondary btn-sm" onclick={() => (policiesOpen = false)} disabled={busy}>Cancel</button>
        <button class="btn-primary btn-sm" disabled={busy} onclick={submitPolicies}>{busy ? 'Saving…' : 'Save policies'}</button>
      </div>
    </div>
  </div>
{/if}
