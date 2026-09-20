<script lang="ts">
  import { onMount } from 'svelte';
  import { getSheet, setSheet, listPool, getTree, orchestraCanisterIds, isPoolUnassigned } from '$lib/api';
  import type { Sheet, PoolReport, Tree } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import SubnetFlags from '$lib/components/SubnetFlags.svelte';
  import AssignPoolCanisterModal from '$lib/components/AssignPoolCanisterModal.svelte';
  let text = $state('');
  let loading = $state(true);
  let error = $state('');
  let busy = $state(false);
  let pool = $state<PoolReport | null>(null);
  let tree = $state<Tree | null>(null);
  let assignPoolTarget = $state<string | null>(null);

  // Parse the editor text into a Sheet, surfacing JSON errors inline.
  let parsed = $derived.by<{ sheet: Sheet | null; err: string }>(() => {
    if (!text.trim()) return { sheet: null, err: 'Sheet is empty' };
    try {
      const obj = JSON.parse(text);
      if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) {
        return { sheet: null, err: 'Sheet must be a JSON object' };
      }
      if (!Array.isArray(obj.sections)) {
        return { sheet: null, err: 'Sheet must have a "sections" array' };
      }
      return { sheet: obj as Sheet, err: '' };
    } catch (e: any) {
      return { sheet: null, err: e?.message ?? 'Invalid JSON' };
    }
  });

  // A compact, deterministic summary of what the sheet declares.
  let summary = $derived.by(() => {
    const s = parsed.sheet;
    if (!s) return null;
    let stands = 0;
    let canisters = 0;
    const manual: string[] = [];
    for (const sec of s.sections ?? []) {
      stands += (sec.stands ?? []).length;
      for (const d of sec.stands ?? []) {
        canisters += (d.canisters ?? []).length;
        // a stand inherits its section's sync unless it says otherwise (#51)
        if ((d.sync ?? sec.sync) === 'manual') manual.push(d.name);
      }
    }
    const bundles = (s.registry?.publish ?? []).length;
    return { sections: (s.sections ?? []).length, stands, canisters, manual, bundles };
  });

  const orchestraCanisterIdSet = $derived.by(() =>
    tree ? orchestraCanisterIds(tree) : new Set<string>(),
  );

  async function load() {
    loading = true;
    error = '';
    try {
      const [sheet, poolReport, treeData] = await Promise.all([
        getSheet(),
        listPool(),
        getTree().catch(() => null),
      ]);
      text = JSON.stringify(sheet, null, 2);
      pool = poolReport;
      tree = treeData;
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function save() {
    if (!parsed.sheet) {
      toasts.error(parsed.err);
      return;
    }
    busy = true;
    try {
      await setSheet(parsed.sheet);
      toasts.success('Sheet saved (persisted)');
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save sheet');
    } finally {
      busy = false;
    }
  }

</script>

<svelte:head><title>Casals · Sheet</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Sheet</h1>
      <p class="text-sm text-primary-500 mt-1 max-w-2xl">
        The desired orchestra as a single editable document (<code class="font-mono">casals.json</code>),
        persisted in the conductor. Saving changes nothing on-chain — apply the sheet from
        <a href="/plan" class="text-primary-700 underline font-medium">Plan / Drift</a>, which shows
        exactly what <code class="font-mono">casals plan</code> would change before you apply it.
      </p>
      <p class="text-xs text-primary-400 mt-1 max-w-2xl">
        Subnet placement is configured in <strong>Settings → Subnet whitelist</strong>.
        A section or stand may set <code class="font-mono">"subnet": "&lt;subnet-id&gt;"</code>
        (or <code class="font-mono">"subnet_type": "fiduciary"</code> when no whitelist is active).
        Stand overrides section. Existing canisters are never moved.
      </p>
    </div>
    <div class="flex items-center gap-2 self-start shrink-0">
      <button class="btn-secondary btn-sm" onclick={load} disabled={loading || busy}>Refresh</button>
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={save} disabled={busy || !parsed.sheet}>{busy ? 'Saving…' : 'Save'}</button>
      {/if}
      <a href="/plan" class="btn-secondary btn-sm">Plan / Drift →</a>
    </div>
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <svg class="w-5 h-5 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      </svg>
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
    <!-- Editor -->
    <div class="lg:col-span-2 space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-xs font-semibold text-primary-500 uppercase tracking-wider">Sheet (JSON)</span>
        {#if summary}
          <span class="text-xs text-primary-400">
            {summary.sections} section(s) · {summary.stands} stand(s) · {summary.canisters} canister(s){#if summary.bundles} · {summary.bundles} bundle(s){/if}
            {#if summary.manual.length}
              <span class="badge bg-amber-50 text-amber-800 border border-amber-200 ml-2" title="sync: manual — observed by the planner, acted upon only when targeted (casals up --stand …)">
                manual: {summary.manual.join(', ')}
              </span>
            {/if}
          </span>
        {/if}
      </div>
      {#if loading}
        <div class="skeleton h-96 w-full rounded-lg"></div>
      {:else}
        <textarea
          bind:value={text}
          spellcheck="false"
          readonly={!$isAuthenticated}
          class="w-full h-[28rem] font-mono text-xs leading-relaxed p-4 rounded-lg border border-[var(--color-border-primary)] bg-white text-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-300 resize-y"
        ></textarea>
        {#if parsed.err}
          <p class="text-xs text-red-600">⚠ {parsed.err}</p>
        {:else}
          <p class="text-xs text-emerald-600">✓ valid sheet</p>
        {/if}
      {/if}
    </div>

    <!-- Pool -->
    <div class="space-y-6">
      <div class="card p-4">
        <h2 class="text-sm font-semibold text-primary-900 mb-3">Canister pool</h2>
        {#if pool}
          <div class="flex items-center gap-4 text-sm">
            <div><span class="text-2xl font-bold text-primary-900">{pool.total}</span><span class="text-xs text-primary-400 ml-1">total</span></div>
            <div><span class="text-2xl font-bold text-emerald-600">{pool.free}</span><span class="text-xs text-primary-400 ml-1">free</span></div>
            <div><span class="text-2xl font-bold text-primary-600">{pool.in_use}</span><span class="text-xs text-primary-400 ml-1">in use</span></div>
          </div>
          <p class="text-xs text-primary-400 mt-2">
            Retired canisters return their canister here for reuse — Casals never deletes a canister it created.
          </p>
          {#if pool.canisters.length > 0}
            <ul class="mt-3 space-y-1 max-h-48 overflow-y-auto">
              {#each pool.canisters as c (c.canister_id)}
                {@const unassigned = isPoolUnassigned(c.canister_id, orchestraCanisterIdSet)}
                <li class="flex items-center justify-between gap-2 text-xs">
                  <span class="font-mono text-primary-600 truncate">{c.canister_id}</span>
                  <span class="flex items-center gap-1.5 shrink-0">
                    {#if c.subnet}
                      <SubnetFlags subnetId={c.subnet} />
                      <span class="badge badge-neutral font-mono" title="subnet {c.subnet}">⬡ {c.subnet.slice(0, 5)}…</span>
                    {:else if c.canister_id}
                      <SubnetFlags canisterId={c.canister_id} />
                    {/if}
                    <span class="badge {c.status === 'free' ? 'badge-frontend' : 'badge-backend'}">
                      {c.status === 'free' ? 'free' : c.canister_name || 'in use'}
                    </span>
                    {#if $isAuthenticated && unassigned}
                      <button
                        type="button"
                        class="btn-secondary btn-sm px-2 py-0.5 text-[11px]"
                        onclick={() => { assignPoolTarget = c.canister_id; }}
                      >
                        Assign
                      </button>
                    {/if}
                  </span>
                </li>
              {/each}
            </ul>
          {/if}
        {:else}
          <div class="skeleton h-12 w-full"></div>
        {/if}
      </div>

    </div>
  </div>
</div>

{#if assignPoolTarget}
  <AssignPoolCanisterModal
    canisterId={assignPoolTarget}
    onsuccess={async () => {
      assignPoolTarget = null;
      const [poolReport, treeData] = await Promise.all([listPool(), getTree().catch(() => null)]);
      pool = poolReport;
      tree = treeData;
    }}
    oncancel={() => { assignPoolTarget = null; }}
  />
{/if}
