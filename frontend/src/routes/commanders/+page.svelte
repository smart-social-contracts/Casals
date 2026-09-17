<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getTree, setCommander, removeCommander, setPermissions, listPermissions, listBackendControllers,
    casalsMetadata,
    type Tree, type Permission,
  } from '$lib/api';
  import { buildPrincipalLabels, controllerLabel } from '$lib/controllerLabels';
  import { entityCommanders, isUnclaimedSlot } from '$lib/commanderAccess';
  import { codeChecksum, generateAccessCode, shortChecksum } from '$lib/accessCode';
  import { identity, isAuthenticated, principal } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import {
    OPERATOR_ACCESS_TABS,
    ORCHESTRA_SECTION,
    assignableSections,
    describeOperatorAccess,
    groupPermissions,
    isOrchestraSectionName,
    scopeLabel,
    type OperatorAccessTab,
    type OperatorScope,
  } from '$lib/governanceUx';
  import GovernanceMapCard from '$lib/components/GovernanceMapCard.svelte';
  import PermissionReferenceTable from '$lib/components/PermissionReferenceTable.svelte';

  interface CommanderRow {
    scope: OperatorScope;
    section: string;          // backing section row ("Casals" for orchestra scope)
    stand?: string;
    principal: string;        // principal, or `sha256:<hex>` for an unclaimed slot
    label: string;            // hierarchy path label
    permissions: string[];    // resolved granted keys
    allPermissions: boolean;  // true => full access ("*")
    unclaimed: boolean;       // access-code slot nobody has redeemed yet
    codeChecksum?: string;    // checksum of the code a claimed commander redeemed
  }

  let tree = $state<Tree | null>(null);
  let catalog = $state<Permission[]>([]);
  let controllerPrincipals = $state<string[]>([]);
  let orchestraName = $state('');
  let loading = $state(true);
  let error = $state('');
  let filterQuery = $state('');
  let activeTab = $state<OperatorAccessTab>('roles');

  async function load() {
    loading = true;
    error = '';
    try {
      const [t, perms, controllers, meta] = await Promise.all([
        getTree(),
        listPermissions().catch(() => []),
        listBackendControllers().catch(() => []),
        casalsMetadata().catch(() => null),
      ]);
      tree = t;
      controllerPrincipals = controllers;
      orchestraName = (meta?.orchestra_name ?? '').trim();
      if (perms.length) catalog = perms;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load data';
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Catalog grouped by group, in declaration order.
  const groupedCatalog = $derived.by(() => groupPermissions(catalog));

  const operatorStatus = $derived.by(() =>
    describeOperatorAccess($principal, controllerPrincipals, rows, orchestraName),
  );

  const labelFor = (key: string) => catalog.find((p) => p.key === key)?.label ?? key;

  const principalLabels = $derived.by(() => buildPrincipalLabels(tree));

  // Flatten tree + Casals backend controllers into commander rows.
  const rows = $derived.by((): CommanderRow[] => {
    const out: CommanderRow[] = [];
    for (const principal of controllerPrincipals) {
      out.push({
        scope: 'controller',
        section: '',
        principal,
        label: 'Casals controller',
        permissions: [],
        allPermissions: true,
        unclaimed: false,
      });
    }
    if (!tree) return out;
    // Orchestra rung first: the backend stores `conductor.commanders` on a
    // synthetic section; those commanders act on every section and stand.
    const sections = [...tree.sections].sort(
      (a, b) => Number(isOrchestraSectionName(b.name)) - Number(isOrchestraSectionName(a.name)),
    );
    for (const sec of sections) {
      const orchestra = isOrchestraSectionName(sec.name);
      for (const cmd of entityCommanders(sec)) {
        const scope: OperatorScope = orchestra ? 'orchestra' : 'section';
        out.push({
          scope, section: sec.name, principal: cmd.principal,
          label: scopeLabel({ scope, section: sec.name }, orchestraName),
          permissions: cmd.permissions ?? [], allPermissions: cmd.all_permissions ?? true,
          unclaimed: isUnclaimedSlot(cmd), codeChecksum: cmd.code_checksum,
        });
      }
      for (const dk of sec.stands) {
        for (const cmd of entityCommanders(dk)) {
          out.push({
            scope: 'stand', section: sec.name, stand: dk.name, principal: cmd.principal,
            label: scopeLabel({ scope: 'stand', section: sec.name, stand: dk.name }, orchestraName),
            permissions: cmd.permissions ?? [], allPermissions: cmd.all_permissions ?? true,
            unclaimed: isUnclaimedSlot(cmd), codeChecksum: cmd.code_checksum,
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
  const sectionOptions = $derived(assignableSections((tree?.sections ?? []).map((s) => s.name)));
  const hasOrchestraRung = $derived((tree?.sections ?? []).some((s) => isOrchestraSectionName(s.name)));
  function standNames(sectionName: string) {
    return (tree?.sections.find((s) => s.name === sectionName)?.stands ?? []).map((d) => d.name);
  }

  let busy = $state(false);

  let assignOpen = $state(false);
  let assignScope = $state<'orchestra' | 'section' | 'stand'>('section');
  let assignSection = $state('');
  let assignStand = $state('');
  let assignPrincipal = $state('');
  let assignPerms = $state<Set<string>>(new Set());
  // 'principal': grant a known principal. 'code': mint an access code; the slot
  // is stored as the code's sha256 checksum until someone redeems it.
  let assignMode = $state<'principal' | 'code'>('principal');
  let assignCode = $state('');
  let assignCodeChecksum = $state('');
  // Set once a code slot was created: the plaintext is shown one last time.
  let mintedCode = $state('');

  async function regenerateAssignCode() {
    assignCode = generateAccessCode();
    assignCodeChecksum = await codeChecksum(assignCode);
  }

  function openAssign() {
    assignScope = 'section';
    assignSection = sectionOptions[0] ?? '';
    assignStand = '';
    assignPrincipal = '';
    assignMode = 'principal';
    assignCode = '';
    assignCodeChecksum = '';
    mintedCode = '';
    // Default a new commander to full access (all permissions checked).
    assignPerms = new Set(catalog.map((p) => p.key));
    assignOpen = true;
  }

  async function setAssignMode(mode: 'principal' | 'code') {
    assignMode = mode;
    if (mode === 'code' && !assignCode) await regenerateAssignCode();
  }

  const assignTargetReady = $derived(
    !(assignScope === 'stand' && !assignStand) && !(assignScope === 'section' && !assignSection),
  );
  const assignReady = $derived(
    assignTargetReady && (assignMode === 'code' ? !!assignCodeChecksum : !!assignPrincipal.trim()),
  );

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
    if (!assignReady) return;
    busy = true;
    try {
      const target = assignScope === 'orchestra'
        ? { section: ORCHESTRA_SECTION }
        : assignScope === 'stand' && assignStand
          ? { stand: assignStand }
          : { section: assignSection };
      const permissions: string[] | '*' = assignAllChecked ? '*' : [...assignPerms];
      const commander_principal = assignMode === 'code' ? assignCodeChecksum : assignPrincipal.trim();
      await setCommander({ ...target, commander_principal, permissions });
      if (assignMode === 'code') {
        // Keep the dialog open: this is the last time the plaintext code is visible.
        mintedCode = assignCode;
        toasts.success('Access code slot created');
      } else {
        toasts.success('Commander assigned');
        assignOpen = false;
      }
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

  async function submitRemove(row: CommanderRow) {
    const where = row.scope === 'orchestra' ? `the orchestra (${row.label})` : `${row.scope} "${row.stand ?? row.section}"`;
    const who = row.unclaimed ? `the pending access code ${shortChecksum(row.principal)}` : `${row.principal.slice(0, 12)}…`;
    if (!confirm(`Remove ${who} from ${where}?`)) return;
    busy = true;
    try {
      const target = row.scope === 'stand' && row.stand
        ? { stand: row.stand }
        : { section: row.section };
      await removeCommander({ ...target, commander_principal: row.principal });
      toasts.success('Commander removed');
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }

  async function submitPerms() {
    if (!permsRow) return;
    busy = true;
    try {
      const target = permsRow.scope === 'stand' ? { stand: permsRow.stand! } : { section: permsRow.section };
      // Collapse a full set to "*" so it reads as full access.
      const permissions: string[] | '*' = allChecked ? '*' : [...permsSelected];
      await setPermissions({ ...target, commander_principal: permsRow.principal, permissions });
      toasts.success('Permissions updated');
      permsOpen = false;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed');
    } finally {
      busy = false;
    }
  }

</script>

<svelte:head><title>Casals · Operator access</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <!-- Header -->
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div class="space-y-2">
      <h1 class="text-2xl font-bold text-primary-900">Operator access</h1>
      <p class="text-sm text-primary-500 max-w-2xl">
        Casals commander roles — who may call Casals APIs, scoped Orchestra → Section → Stand.
        A commander at one rung acts on everything beneath it.
        This is separate from the <a href="/multisig" class="text-primary-700 underline">platform committee</a>
        (on-chain multisig).
      </p>
      {#if $isAuthenticated}
        <p class="text-xs text-primary-600 border border-primary-100 bg-primary-50 rounded-lg px-3 py-2 max-w-2xl">
          <span class="font-medium">Your access:</span> {operatorStatus}
        </p>
      {/if}
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

  <div class="flex flex-wrap gap-2 border-b border-primary-100 pb-1">
    {#each OPERATOR_ACCESS_TABS as tab (tab.id)}
      <button
        type="button"
        class="px-3 py-2 text-sm rounded-t-lg border-b-2 transition-colors
          {activeTab === tab.id
            ? 'border-primary-600 text-primary-900 font-medium'
            : 'border-transparent text-primary-500 hover:text-primary-800'}"
        onclick={() => (activeTab = tab.id)}
      >
        {tab.label}
      </button>
    {/each}
  </div>
  <p class="text-xs text-primary-400 -mt-3">
    {OPERATOR_ACCESS_TABS.find((t) => t.id === activeTab)?.hint ?? ''}
  </p>

  {#if activeTab === 'roles'}
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
        {@const unclaimed = pRows.every((r) => r.unclaimed)}
        {@const pl = unclaimed
          ? { display: 'Pending access code', title: principal }
          : controllerLabel(principal, principalLabels)}
        <div class="card overflow-hidden {unclaimed ? 'border-dashed' : ''}">
          <!-- Principal header -->
          <div class="flex items-center gap-3 px-4 py-3 bg-primary-50/60 border-b border-primary-100">
            <div class="w-9 h-9 rounded-full {unclaimed ? 'bg-amber-50' : 'bg-primary-100'} flex items-center justify-center shrink-0">
              {#if unclaimed}
                <svg class="w-5 h-5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 5.25a3 3 0 0 1 3 3m3 0a6 6 0 0 1-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1 1 21.75 8.25z" />
                </svg>
              {:else}
                <svg class="w-5 h-5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
                </svg>
              {/if}
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 min-w-0">
                <div class="font-medium text-sm text-primary-900 truncate" title={pl.title}>{pl.display}</div>
                {#if pRows.some((r) => r.scope === 'controller')}
                  <span class="badge shrink-0 bg-amber-50 text-amber-800 border border-amber-200">controller</span>
                {/if}
                {#if unclaimed}
                  <span class="badge shrink-0 bg-amber-50 text-amber-800 border border-amber-200">unclaimed</span>
                {:else if pRows.some((r) => r.codeChecksum)}
                  <span class="badge shrink-0 badge-neutral" title="Joined by redeeming an access code">via code</span>
                {/if}
              </div>
              <div class="font-mono text-xs text-primary-400 truncate mt-0.5" title={pl.title}>
                {unclaimed ? shortChecksum(principal) : pl.title}
              </div>
              <div class="text-xs text-primary-400 mt-0.5">
                {#if unclaimed}
                  Whoever redeems this code takes {pRows.length} role{pRows.length !== 1 ? 's' : ''}
                {:else}
                  {pRows.length} role{pRows.length !== 1 ? 's' : ''}
                {/if}
              </div>
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
                    : row.scope === 'orchestra'
                      ? 'bg-primary-800 text-white border border-primary-800'
                      : row.scope === 'section' ? 'badge-primary' : 'badge-neutral'}">{row.scope}</span>
                  <span class="text-sm text-primary-800 flex-1 truncate">
                    {#if row.scope === 'controller'}
                      <span class="font-medium">{row.label}</span>
                      <span class="text-primary-400 ml-1">· full platform access</span>
                    {:else if row.scope === 'orchestra'}
                      <span class="font-medium">{row.label}</span>
                      <span class="text-primary-400 ml-1">· every section and stand</span>
                    {:else if row.stand}
                      <span class="text-primary-500">{row.section}</span><span class="text-primary-300 mx-1">/</span><span class="font-medium">{row.stand}</span>
                    {:else}
                      <span class="font-medium">{row.section}</span>
                    {/if}
                  </span>
                  {#if $isAuthenticated && row.scope !== 'controller'}
                    <button class="btn-ghost btn-sm text-xs shrink-0" onclick={() => openPerms(row)}>Permissions</button>
                    <button class="btn-ghost btn-sm text-xs shrink-0 text-red-600 hover:text-red-700" onclick={() => submitRemove(row)} disabled={busy}>Remove</button>
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
  {/if}

  {#if activeTab === 'reference'}
    <GovernanceMapCard />
    <div class="card p-4">
      <h2 class="text-sm font-semibold text-primary-900 mb-3">Casals commander permissions</h2>
      <PermissionReferenceTable catalog={catalog} />
    </div>
  {/if}
</div>

<!-- Assign commander modal -->
{#if assignOpen}
  <div class="fixed inset-0 z-40 flex items-center justify-center">
    <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={() => (assignOpen = false)}></button>
    <div class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 space-y-4 max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-semibold text-primary-900">Grant operator access</h3>
      <p class="text-sm text-primary-500">
        Adds a commander without removing existing ones. Casals orchestration API permissions do not make someone a multisig signer.
        <button type="button" class="underline text-primary-600" onclick={() => { assignOpen = false; activeTab = 'reference'; }}>View all permissions</button>
      </p>
      <div>
        <span class="label">Scope</span>
        <div class="flex gap-2 mt-1">
          {#if hasOrchestraRung}
            <button class="btn-sm {assignScope === 'orchestra' ? 'btn-primary' : 'btn-secondary'}" onclick={() => { assignScope = 'orchestra'; assignStand = ''; }}>Orchestra</button>
          {/if}
          <button class="btn-sm {assignScope === 'section' ? 'btn-primary' : 'btn-secondary'}" onclick={() => { assignScope = 'section'; assignStand = ''; }}>Section</button>
          <button class="btn-sm {assignScope === 'stand' ? 'btn-primary' : 'btn-secondary'}" onclick={() => (assignScope = 'stand')}>Stand</button>
        </div>
        {#if assignScope === 'orchestra'}
          <p class="text-xs text-primary-500 mt-2">
            Orchestra commanders{orchestraName ? ` on ${orchestraName}` : ''} act on every section and stand
            (stored as <code>conductor.commanders</code> in the sheet).
          </p>
        {/if}
      </div>
      {#if assignScope !== 'orchestra'}
        <div>
          <label class="label" for="assign-section">Section</label>
          <select id="assign-section" class="input" bind:value={assignSection}>
            {#each sectionOptions as name (name)}<option value={name}>{name}</option>{/each}
          </select>
        </div>
      {/if}
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
        <span class="label">Who</span>
        <div class="flex gap-2 mt-1">
          <button class="btn-sm {assignMode === 'principal' ? 'btn-primary' : 'btn-secondary'}" onclick={() => setAssignMode('principal')} disabled={!!mintedCode}>Known principal</button>
          <button class="btn-sm {assignMode === 'code' ? 'btn-primary' : 'btn-secondary'}" onclick={() => setAssignMode('code')} disabled={!!mintedCode}>Access code</button>
        </div>
      </div>
      {#if assignMode === 'principal'}
        <div>
          <label class="label" for="assign-principal">Principal</label>
          <input id="assign-principal" type="text" class="input font-mono text-sm" placeholder="aaaaa-aa…" bind:value={assignPrincipal} />
        </div>
      {:else if mintedCode}
        <div class="rounded-lg border border-emerald-200 bg-emerald-50 p-3 space-y-2">
          <p class="text-sm font-medium text-emerald-900">Slot created. Hand this code to the new operator:</p>
          <div class="flex items-center gap-2">
            <code class="flex-1 min-w-0 rounded border border-emerald-200 bg-white px-3 py-2 font-mono text-base tracking-wider text-primary-900 break-all select-all">{mintedCode}</code>
            <button class="btn-secondary btn-sm shrink-0" onclick={() => copyToClipboard(mintedCode)}>Copy</button>
          </div>
          <p class="text-xs text-emerald-800">
            This is the only time the code is shown; Casals stores just its checksum
            (<span class="font-mono">{shortChecksum(assignCodeChecksum)}</span>). The recipient logs in, sees the
            Access Denied dialog and enters the code there. It works once.
          </p>
        </div>
      {:else}
        <div class="rounded-lg border border-primary-100 bg-primary-50/60 p-3 space-y-2">
          <div class="flex items-center gap-2">
            <code class="flex-1 min-w-0 rounded border border-primary-200 bg-white px-3 py-2 font-mono text-base tracking-wider text-primary-900 break-all select-all">{assignCode || '…'}</code>
            <button class="btn-secondary btn-sm shrink-0" onclick={regenerateAssignCode} aria-label="Generate another code">↻</button>
          </div>
          <p class="text-xs text-primary-500">
            Generated in your browser. Only its checksum
            <span class="font-mono">{assignCodeChecksum ? shortChecksum(assignCodeChecksum) : '…'}</span>
            is sent to Casals; whoever redeems the code becomes this commander. Copy it after the slot is created.
          </p>
        </div>
      {/if}

      <!-- Permissions -->
      <div class="border-t border-primary-100 pt-3 space-y-3 {mintedCode ? 'opacity-60 pointer-events-none' : ''}">
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
          {#if mintedCode}
            <button class="btn-primary btn-sm" onclick={() => (assignOpen = false)}>Done</button>
          {:else}
            <button class="btn-secondary btn-sm" onclick={() => (assignOpen = false)} disabled={busy}>Cancel</button>
            <button class="btn-primary btn-sm" disabled={busy || !assignReady} onclick={submitAssign}>
              {busy ? (assignMode === 'code' ? 'Creating…' : 'Assigning…') : (assignMode === 'code' ? 'Create code slot' : 'Assign')}
            </button>
          {/if}
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
          {permsRow.scope === 'stand' ? `Stand "${permsRow.stand}"` : permsRow.scope === 'orchestra' ? permsRow.label : `Section "${permsRow.section}"`} ·
          <span class="font-mono">{permsRow.unclaimed ? `pending code ${shortChecksum(permsRow.principal)}` : `${permsRow.principal.slice(0, 12)}…`}</span>
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

