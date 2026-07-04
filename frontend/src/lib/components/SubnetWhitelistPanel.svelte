<script lang="ts">
  import { onMount } from 'svelte';
  import SubnetFlagMatrix from '$lib/components/SubnetFlagMatrix.svelte';
  import { listSubnetPlacement, setSubnetWhitelist } from '$lib/api';
  import {
    buildRegionGroups,
    getSubnetGeo,
    listIcSubnets,
    subnetShortLabel,
    unionCountryColumns,
    warmSubnetGeoCache,
    type SubnetGeo,
  } from '$lib/subnetGeo';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  interface Props {
    whitelist: string[];
    canEdit: boolean;
    onsaved?: (whitelist: string[]) => void;
  }

  let { whitelist = [], canEdit = false, onsaved }: Props = $props();

  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');
  let allSubnetIds = $state<string[]>([]);
  let creatableSubnetIds = $state<Set<string>>(new Set());
  let geoById = $state<Record<string, SubnetGeo | null>>({});
  let selected = $state<Set<string>>(new Set());
  let copiedSubnetId = $state<string | null>(null);

  type SortKey = 'available' | 'subnet' | 'type' | 'countries' | 'nodes';
  type SortDir = 'asc' | 'desc';

  let sortKey = $state<SortKey>('available');
  let sortDir = $state<SortDir>('asc');
  let showUnavailable = $state(false);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortKey = key;
      sortDir = key === 'subnet' || key === 'type' ? 'asc' : 'desc';
    }
  }

  function sortIndicator(key: SortKey): string {
    if (sortKey !== key) return '';
    return sortDir === 'asc' ? ' ↑' : ' ↓';
  }

  function countryCount(geo: SubnetGeo | null | undefined): number {
    return geo?.countryCodes.length ?? 0;
  }

  function nodeCount(geo: SubnetGeo | null | undefined): number | null {
    return geo?.nodeCount != null ? geo.nodeCount : null;
  }

  function compareRows(a: string, b: string): number {
    const ga = geoById[a];
    const gb = geoById[b];
    let cmp = 0;

    switch (sortKey) {
      case 'available':
        cmp = Number(isCreatable(a)) - Number(isCreatable(b));
        break;
      case 'subnet':
        cmp = subnetShortLabel(a).localeCompare(subnetShortLabel(b));
        if (cmp === 0) cmp = a.localeCompare(b);
        break;
      case 'type':
        cmp = (ga?.subnetType || '').localeCompare(gb?.subnetType || '');
        break;
      case 'countries':
        cmp = countryCount(ga) - countryCount(gb);
        break;
      case 'nodes': {
        const na = nodeCount(ga);
        const nb = nodeCount(gb);
        cmp = (na ?? -1) - (nb ?? -1);
        break;
      }
    }

    if (cmp === 0) cmp = a.localeCompare(b);
    return sortDir === 'asc' ? cmp : -cmp;
  }

  function isCreatable(id: string): boolean {
    return creatableSubnetIds.has(id);
  }

  function creatableSubnetList(): string[] {
    return allSubnetIds.filter(isCreatable);
  }

  function selectionFromWhitelist(stored: string[]): Set<string> {
    if (stored.length > 0) return new Set(stored);
    return new Set(creatableSubnetList());
  }

  function subnetsToPersist(selectedCreatable: string[]): string[] {
    const allCreatable = creatableSubnetList();
    if (
      selectedCreatable.length === allCreatable.length &&
      allCreatable.every((id) => selectedCreatable.includes(id))
    ) {
      return [];
    }
    return selectedCreatable;
  }

  $effect(() => {
    selected = selectionFromWhitelist(whitelist);
  });

  async function copySubnetId(id: string) {
    if (await copyText(id)) {
      copiedSubnetId = id;
      toasts.success('Subnet ID copied');
      setTimeout(() => {
        if (copiedSubnetId === id) copiedSubnetId = null;
      }, 1500);
    } else {
      toasts.error('Copy failed');
    }
  }

  async function load() {
    loading = true;
    error = '';
    try {
      const [icSubnets, placement] = await Promise.all([
        listIcSubnets(),
        listSubnetPlacement().catch(() => ({ creatable_subnets: [] as string[] })),
      ]);
      allSubnetIds = icSubnets.map((s) => s.subnetId);
      creatableSubnetIds = new Set(placement.creatable_subnets ?? []);

      // Seed authorization from the list response before per-subnet detail fetches.
      const entries: Record<string, SubnetGeo | null> = {};
      for (const row of icSubnets) {
        entries[row.subnetId] = {
          subnetId: row.subnetId,
          countryCodes: [],
          flags: [],
          countryNames: [],
          orderedCountries: [],
          dataCenters: [],
          subnetType: row.subnetType,
          subnetAuthorization: row.subnetAuthorization,
        };
      }

      await warmSubnetGeoCache(allSubnetIds);
      await Promise.all(
        allSubnetIds.map(async (id) => {
          const geo = await getSubnetGeo(id);
          if (geo) {
            entries[id] = {
              ...geo,
              subnetAuthorization: geo.subnetAuthorization ?? entries[id]?.subnetAuthorization,
            };
          }
        }),
      );
      geoById = entries;
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  function toggle(id: string) {
    if (!canEdit || !isCreatable(id)) return;
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
  }

  function selectAll() {
    if (!canEdit) return;
    selected = new Set(allSubnetIds.filter(isCreatable));
  }

  function clearAll() {
    if (!canEdit) return;
    selected = new Set();
  }

  async function save() {
    saving = true;
    try {
      const selectedCreatable = allSubnetIds.filter((id) => selected.has(id) && isCreatable(id));
      const subnets = subnetsToPersist(selectedCreatable);
      const r = await setSubnetWhitelist(subnets);
      const saved = r.subnet_whitelist ?? subnets;
      toasts.success(
        saved.length
          ? `Saved whitelist (${saved.length} subnet${saved.length === 1 ? '' : 's'})`
          : `Saved — all ${selectedCreatable.length} available subnets allowed`,
      );
      onsaved?.(saved);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save subnet whitelist');
    } finally {
      saving = false;
    }
  }

  const sortedRows = $derived.by(() => [...allSubnetIds].sort(compareRows));

  const visibleRows = $derived(
    sortedRows.filter((id) => showUnavailable || isCreatable(id)),
  );

  const regionGroups = $derived(
    buildRegionGroups(unionCountryColumns(visibleRows.map((id) => geoById[id]))),
  );

  const creatableCount = $derived(allSubnetIds.filter(isCreatable).length);
  const unavailableCount = $derived(allSubnetIds.length - creatableCount);
  const selectedCreatableCount = $derived(
    [...selected].filter((id) => isCreatable(id)).length,
  );
  const baselineSelection = $derived(selectionFromWhitelist(whitelist));
  const hasUnsavedChanges = $derived.by(() => {
    const current = new Set([...selected].filter(isCreatable));
    if (baselineSelection.size !== current.size) return true;
    for (const id of baselineSelection) {
      if (!current.has(id)) return true;
    }
    return false;
  });

  onMount(load);
</script>

<div class="space-y-4 w-full pb-5">
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
    <div>
      <h3 class="text-sm font-semibold text-primary-800">Subnet whitelist</h3>
      <p class="text-xs text-primary-400 mt-0.5 max-w-xl">
        Choose which subnets may host new orchestra canisters. By default all available subnets
        are whitelisted. When the saved list is a strict subset, only checked subnets are allowed
        — sheet <code class="font-mono">subnet</code> targets must be on the list.
      </p>
    </div>
    <div class="flex items-center gap-2 shrink-0">
      <button type="button" class="btn-secondary btn-sm" onclick={load} disabled={loading}>
        {loading ? 'Loading…' : 'Refresh'}
      </button>
      {#if canEdit}
        <button
          type="button"
          class="btn-secondary btn-sm"
          onclick={selectAll}
          disabled={loading || !creatableCount}
        >
          Select all available
        </button>
        <button type="button" class="btn-secondary btn-sm" onclick={clearAll} disabled={loading}>
          Clear
        </button>
        <button type="button" class="btn-primary btn-sm" onclick={save} disabled={saving || loading}>
          {saving ? 'Saving…' : 'Save whitelist'}
        </button>
      {/if}
    </div>
  </div>

  {#if error}
    <p class="text-xs text-red-600">{error}</p>
  {/if}

  {#if !canEdit}
    <p class="text-xs text-primary-500 bg-primary-50 rounded-lg px-3 py-2">
      You need the <span class="font-mono">subnet.whitelist</span> permission on a section
      or stand you command (section commanders with
      <span class="font-mono">commander.assign</span> also qualify). Casals controllers
      may always edit this list.
    </p>
  {/if}

  <details class="group rounded-lg border border-[var(--color-border-primary)] bg-primary-50/50 px-3 py-2.5">
    <summary class="cursor-pointer text-xs font-medium text-primary-700 select-none list-none [&::-webkit-details-marker]:hidden">
      <span class="inline-flex items-center gap-1.5">
        <span class="text-primary-400 group-open:rotate-90 transition-transform" aria-hidden="true">▸</span>
        Subnet legend
      </span>
    </summary>
    <dl class="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2 text-xs text-primary-600">
      <div>
        <dt class="font-medium text-primary-800 mb-0.5">Availability</dt>
        <dd class="space-y-2 text-primary-500">
          <p class="flex items-start gap-2">
            <span class="mt-1.5 h-2 w-2 shrink-0 rounded-sm bg-white ring-1 ring-primary-200" aria-hidden="true"></span>
            <span><strong class="text-primary-700">Available</strong> — Casals can create new orchestra
              canisters here (CMC default subnet, or explicitly authorized for this Casals canister).</span>
          </p>
          <p class="flex items-start gap-2">
            <span class="mt-1.5 h-2 w-2 shrink-0 rounded-sm bg-primary-100 opacity-60 ring-1 ring-primary-200" aria-hidden="true"></span>
            <span><strong class="text-primary-700">Unavailable (grayed)</strong> — not selectable.
              Casals cannot create canisters on this subnet via the CMC.</span>
          </p>
        </dd>
      </div>
      <div>
        <dt class="font-medium text-primary-800 mb-0.5">IC Dashboard authorization</dt>
        <dd class="space-y-1.5 text-primary-500">
          <p><strong class="text-primary-700">Public</strong> — open for default canister creation;
            usually available here.</p>
          <p><strong class="text-primary-700">Authorized only</strong> — outside CMC defaults; new
            canisters need NNS-granted authorization for the creating principal. Usually grayed out.</p>
        </dd>
      </div>
      <div>
        <dt class="font-medium text-primary-800 mb-0.5">Subnet type</dt>
        <dd class="space-y-1.5 text-primary-500">
          <p><span class="font-mono uppercase text-[10px] text-primary-400">application</span> —
            standard 13-node subnet for user canisters (1× cycle cost).</p>
          <p><span class="font-mono uppercase text-[10px] text-primary-400">verified_application</span> —
            legacy launch-era label; behaves like <span class="font-mono">application</span> today.</p>
          <p><span class="font-mono uppercase text-[10px] text-primary-400">system</span> —
            infrastructure subnet (NNS, II, etc.); not for user canisters.</p>
        </dd>
      </div>
      <div>
        <dt class="font-medium text-primary-800 mb-0.5">Other labels</dt>
        <dd class="space-y-1.5 text-primary-500">
          <p><strong class="text-primary-700">Country flags</strong> — node data-center locations in
            every row (Americas · Europe · Mid East · APAC). Full color = present in that subnet;
            gray = absent. Order is consistent across all rows.</p>
          <p><strong class="text-primary-700">Specializations</strong> — some subnets also carry
            tags like <span class="font-mono">fiduciary</span> (higher replication) or
            <span class="font-mono">european</span> (EU-only nodes). See the
            <a
              href="https://dashboard.internetcomputer.org/subnets"
              target="_blank"
              rel="noopener noreferrer"
              class="text-primary-600 underline hover:text-primary-800"
            >IC Dashboard</a> for details.</p>
        </dd>
      </div>
    </dl>
  </details>

  {#if loading && !allSubnetIds.length}
    <div class="space-y-2">
      {#each [1, 2, 3, 4] as n (n)}
        <div class="skeleton h-10 w-full rounded-md"></div>
      {/each}
    </div>
  {:else if visibleRows.length === 0 && !showUnavailable && unavailableCount > 0}
    <p class="text-sm text-primary-400">
      {unavailableCount} unavailable subnet{unavailableCount === 1 ? '' : 's'} hidden.
      <button type="button" class="text-primary-600 underline hover:text-primary-800" onclick={() => (showUnavailable = true)}>
        Show them
      </button>
    </p>
  {:else if sortedRows.length === 0}
    <p class="text-sm text-primary-400">No subnets reported by the IC Dashboard API.</p>
  {:else}
    <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
      <p class="text-xs text-primary-500">
        {selectedCreatableCount} of {creatableCount} available selected
        · showing {visibleRows.length} of {sortedRows.length} subnet{sortedRows.length === 1 ? '' : 's'}
        {#if hasUnsavedChanges}
          <span class="text-amber-600">(unsaved changes)</span>
        {/if}
      </p>
      {#if unavailableCount > 0}
        <label class="inline-flex items-center gap-2 text-xs text-primary-600 cursor-pointer shrink-0 select-none">
          <input
            type="checkbox"
            class="w-3.5 h-3.5 rounded border-primary-300"
            bind:checked={showUnavailable}
          />
          Show unavailable ({unavailableCount})
        </label>
      {/if}
    </div>
    <div class="subnet-table-wrap w-full overflow-auto border border-[var(--color-border-primary)] rounded-lg max-h-[32rem]">
      <table class="subnet-table w-full min-w-max border-collapse text-left">
        {#if regionGroups.length}
          <thead>
            <tr class="border-b border-[var(--color-border-primary)] bg-white shadow-[0_1px_0_var(--color-border-primary)]">
              <th class="subnet-col-check sticky top-0 z-10 bg-white px-3 py-2" scope="col">
                <button
                  type="button"
                  class="sort-header"
                  aria-sort={sortKey === 'available' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  onclick={() => toggleSort('available')}
                  title="Sort by availability"
                >
                  Avail{sortIndicator('available')}
                </button>
              </th>
              <th class="subnet-col-flags sticky top-0 z-10 bg-white px-3 py-2" scope="col">
                <SubnetFlagMatrix groups={regionGroups} variant="labels" size="md" />
              </th>
              <th class="subnet-col-subnet sticky top-0 z-10 bg-white px-3 py-2" scope="col">
                <button
                  type="button"
                  class="sort-header"
                  aria-sort={sortKey === 'subnet' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  onclick={() => toggleSort('subnet')}
                  title="Sort by subnet prefix"
                >
                  Subnet{sortIndicator('subnet')}
                </button>
              </th>
              <th class="subnet-col-type sticky top-0 z-10 bg-white px-3 py-2" scope="col">
                <button
                  type="button"
                  class="sort-header"
                  aria-sort={sortKey === 'type' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  onclick={() => toggleSort('type')}
                  title="Sort by subnet type"
                >
                  Type{sortIndicator('type')}
                </button>
              </th>
              <th class="subnet-col-num sticky top-0 z-10 bg-white px-3 py-2 text-right" scope="col">
                <button
                  type="button"
                  class="sort-header sort-header-right"
                  aria-sort={sortKey === 'countries' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  onclick={() => toggleSort('countries')}
                  title="Sort by country count"
                >
                  Countries{sortIndicator('countries')}
                </button>
              </th>
              <th class="subnet-col-num sticky top-0 z-10 bg-white px-3 py-2 text-right" scope="col">
                <button
                  type="button"
                  class="sort-header sort-header-right"
                  aria-sort={sortKey === 'nodes' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  onclick={() => toggleSort('nodes')}
                  title="Sort by node count"
                >
                  Nodes{sortIndicator('nodes')}
                </button>
              </th>
              <th class="subnet-col-copy sticky top-0 z-10 bg-white px-3 py-2" scope="col"></th>
            </tr>
          </thead>
        {/if}
        <tbody class="divide-y divide-[var(--color-border-primary)]">
          {#each visibleRows as id (id)}
            {@const geo = geoById[id]}
            {@const available = isCreatable(id)}
            {@const authOnly = geo?.subnetAuthorization === 'authorized_only'}
            <tr
              class="{available ? 'bg-white hover:bg-primary-50/40' : 'bg-primary-50/60 opacity-60'}"
              title={available
                ? undefined
                : 'Casals cannot create canisters on this subnet via the CMC (authorized-only, not in CMC defaults for this Casals canister).'}
            >
              <td class="subnet-col-check px-3 py-2 align-middle">
                {#if canEdit}
                  <input
                    type="checkbox"
                    class="w-4 h-4 rounded border-primary-300 disabled:opacity-40"
                    checked={selected.has(id)}
                    disabled={!available}
                    onchange={() => toggle(id)}
                  />
                {:else}
                  <span
                    class="block w-4 text-center text-xs {selected.has(id) ? (available ? 'text-emerald-600' : 'text-amber-600') : 'text-primary-300'}"
                  >
                    {selected.has(id) ? '✓' : '·'}
                  </span>
                {/if}
              </td>
              <td class="subnet-col-flags px-3 py-2 align-middle">
                {#if regionGroups.length}
                  <SubnetFlagMatrix
                    groups={regionGroups}
                    presentCodes={geo?.countryCodes ?? []}
                    size="md"
                  />
                {:else}
                  <span class="text-xs text-primary-300">…</span>
                {/if}
              </td>
              <td class="subnet-col-subnet px-3 py-2 align-middle">
                <div
                  class="font-mono text-sm font-medium {available ? 'text-primary-800' : 'text-primary-500'}"
                  title={id}
                >
                  {subnetShortLabel(id)}
                </div>
                {#if !available}
                  <div class="text-[10px] text-primary-400 mt-0.5">
                    {#if authOnly}
                      Authorized only · unavailable
                    {:else}
                      Unavailable
                    {/if}
                  </div>
                {/if}
              </td>
              <td class="subnet-col-type px-3 py-2 align-middle">
                {#if geo?.subnetType}
                  <span class="text-[10px] text-primary-500 uppercase tracking-wide">{geo.subnetType}</span>
                {:else}
                  <span class="text-xs text-primary-300">—</span>
                {/if}
              </td>
              <td class="subnet-col-num px-3 py-2 align-middle text-right tabular-nums text-xs text-primary-700">
                {countryCount(geo)}
              </td>
              <td class="subnet-col-num px-3 py-2 align-middle text-right tabular-nums text-xs text-primary-700">
                {nodeCount(geo) ?? '—'}
              </td>
              <td class="subnet-col-copy px-3 py-2 align-middle">
                <button
                  type="button"
                  class="icon-btn {copiedSubnetId === id ? 'text-emerald-600' : ''}"
                  title="Copy subnet ID"
                  aria-label="Copy subnet ID"
                  onclick={() => copySubnetId(id)}
                >
                  {#if copiedSubnetId === id}
                    <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                  {:else}
                    <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3" />
                    </svg>
                  {/if}
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .subnet-table-wrap {
    /* Full-bleed within the settings card so the table uses available horizontal space. */
    width: calc(100% + 2.5rem);
    margin-left: -1.25rem;
    margin-right: -1.25rem;
    border-left: none;
    border-right: none;
    border-radius: 0;
  }

  .subnet-col-check {
    width: 3.25rem;
  }

  .subnet-col-copy {
    width: 2.75rem;
  }

  .subnet-col-subnet {
    width: 5.5rem;
  }

  .subnet-col-type {
    width: 9rem;
  }

  .subnet-col-num {
    width: 5.5rem;
  }

  .sort-header {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--color-text-primary, #64748b);
    white-space: nowrap;
    padding: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    text-align: left;
  }

  .sort-header:hover {
    color: var(--color-text-primary, #1e293b);
  }

  .sort-header-right {
    width: 100%;
    text-align: right;
  }
</style>
