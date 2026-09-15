<script lang="ts">
  import { onMount } from 'svelte';
  import { getPlan, lastApply, planOrchestra, applyPlan, shortHash } from '$lib/api';
  import type { Plan, PlanItem, ApplyResult } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  const APPLY_BATCH = 5;

  let plan = $state<Plan | null>(null);
  let lastApplied = $state<ApplyResult | null>(null);
  let loading = $state(true);
  let planning = $state(false);
  let applying = $state(false);
  let error = $state('');
  let notice = $state('');
  let confirmDestructive = $state(false);
  let expanded = $state<Record<string, boolean>>({});
  let open = $state<Record<string, boolean>>({ drift: true, unmanaged: false, unverifiable: false, info: false });

  const hasDestructive = $derived(plan?.items.some((i) => i.destructive) ?? false);
  const canApply = $derived(
    $isAuthenticated
      && (plan?.items.length ?? 0) > 0
      && !planning
      && !applying
      && (!hasDestructive || confirmDestructive),
  );

  const REQUIRES_BADGE: Record<string, string> = {
    self: 'badge-frontend',
    multisig: 'badge-wasm-multisig',
    operator: 'badge-backend',
  };

  async function load() {
    loading = true;
    error = '';
    try {
      [plan, lastApplied] = await Promise.all([getPlan(), lastApply()]);
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  async function runPlan() {
    planning = true;
    error = '';
    try {
      plan = await planOrchestra();
      expanded = {};
      confirmDestructive = false;
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      planning = false;
    }
  }

  async function runApply() {
    if (!plan) return;
    applying = true;
    error = '';
    notice = '';
    try {
      lastApplied = await applyPlan({
        plan_hash: plan.hash,
        max_items: APPLY_BATCH,
        confirm_destructive: confirmDestructive,
      });
      if (lastApplied.failed) {
        toasts.error(`Apply stopped at #${lastApplied.failed.seq}: ${lastApplied.failed.kind}`);
      } else {
        toasts.success(`Applied ${lastApplied.applied.length} item(s), ${lastApplied.remaining} remaining`);
      }
      await runPlan();
    } catch (e: any) {
      const msg: string = e?.message ?? String(e);
      if (msg.startsWith('stale plan') || msg.startsWith('busy:')) {
        notice = `${msg} — re-planning.`;
        await runPlan();
      } else if (msg.startsWith('apply requires proposal')) {
        notice = `This environment applies only through a multisig ApplySheet proposal; the conductor will not apply directly. (${msg})`;
      } else if (msg.includes('confirm_destructive')) {
        notice = 'This plan contains destructive items — tick "Confirm destructive items" before applying.';
      } else {
        error = msg;
      }
    } finally {
      applying = false;
    }
  }

  onMount(load);

  function targetLabel(t: PlanItem['target']): string {
    return t.name || t.canister_id || t.stand || t.section || '—';
  }

  function scopeLabel(t: PlanItem['target']): string {
    return [t.section, t.stand].filter(Boolean).join(' / ');
  }

  function fmtNs(ns: number): string {
    return ns ? new Date(ns / 1e6).toLocaleString() : '';
  }

  function json(v: unknown): string {
    return JSON.stringify(v ?? {}, null, 2);
  }

  function toggle(key: string) {
    expanded = { ...expanded, [key]: !expanded[key] };
  }
</script>

<svelte:head><title>Casals · Plan / Drift</title></svelte:head>

{#snippet itemTable(items: PlanItem[], prefix: string)}
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="text-left text-[10px] font-semibold uppercase tracking-wider text-primary-400 border-b border-[var(--color-border-primary)]">
          <th class="py-2 pl-4 pr-2 w-8">#</th>
          <th class="py-2 px-2">Kind</th>
          <th class="py-2 px-2">Target</th>
          <th class="py-2 px-2">Reason</th>
          <th class="py-2 px-2">Requires</th>
          <th class="py-2 px-2 w-8"></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-[var(--color-border-primary)]">
        {#each items as item (`${prefix}:${item.seq}`)}
          {@const key = `${prefix}:${item.seq}`}
          <tr class="align-top hover:bg-primary-50/40">
            <td class="py-2 pl-4 pr-2 font-mono text-xs text-primary-400">{item.seq}</td>
            <td class="py-2 px-2 font-mono text-xs text-primary-800 whitespace-nowrap">{item.kind}</td>
            <td class="py-2 px-2 min-w-0">
              <div class="font-medium text-primary-900">{targetLabel(item.target)}</div>
              {#if scopeLabel(item.target)}
                <div class="text-xs text-primary-400">{scopeLabel(item.target)}</div>
              {/if}
              {#if item.target.canister_id}
                <div class="font-mono text-[11px] text-primary-400 truncate" title={item.target.canister_id}>{item.target.canister_id}</div>
              {/if}
            </td>
            <td class="py-2 px-2 text-xs text-primary-600">{item.reason}</td>
            <td class="py-2 px-2">
              <span class="flex flex-wrap gap-1">
                <span class="badge {REQUIRES_BADGE[item.requires] ?? 'badge-neutral'}">{item.requires}</span>
                {#if item.destructive}
                  <span class="badge badge-critical">destructive</span>
                {/if}
              </span>
            </td>
            <td class="py-2 px-2 text-right">
              <button
                type="button"
                class="icon-btn"
                aria-expanded={!!expanded[key]}
                aria-label="{expanded[key] ? 'Hide' : 'Show'} diff for item {item.seq}"
                onclick={() => toggle(key)}
              >
                <svg class="w-4 h-4 transition-transform {expanded[key] ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </button>
            </td>
          </tr>
          {#if expanded[key]}
            <tr class="bg-primary-50/40">
              <td colspan="6" class="px-4 py-3">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400 mb-1">current</p>
                    <pre class="text-[11px] font-mono leading-relaxed bg-white border border-[var(--color-border-primary)] rounded-lg p-3 overflow-x-auto max-h-72">{json(item.current)}</pre>
                  </div>
                  <div>
                    <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400 mb-1">desired</p>
                    <pre class="text-[11px] font-mono leading-relaxed bg-white border border-[var(--color-border-primary)] rounded-lg p-3 overflow-x-auto max-h-72">{json(item.desired)}</pre>
                  </div>
                </div>
                {#if item.call && Object.keys(item.call).length}
                  <details class="mt-2 text-[11px] text-primary-400">
                    <summary class="cursor-pointer">call</summary>
                    <pre class="mt-1 font-mono bg-white border border-[var(--color-border-primary)] rounded-lg p-3 overflow-x-auto max-h-48">{json(item.call)}</pre>
                  </details>
                {/if}
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  </div>
{/snippet}

{#snippet section(id: string, title: string, count: number)}
  <button
    type="button"
    class="w-full flex items-center justify-between gap-3 px-4 py-3 text-left"
    aria-expanded={!!open[id]}
    onclick={() => (open = { ...open, [id]: !open[id] })}
  >
    <span class="flex items-center gap-2">
      <svg class="w-4 h-4 text-primary-400 transition-transform {open[id] ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
      </svg>
      <span class="text-sm font-semibold text-primary-900">{title}</span>
    </span>
    <span class="badge badge-neutral">{count}</span>
  </button>
{/snippet}

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Plan / Drift</h1>
      <p class="text-sm text-primary-500 mt-1 max-w-2xl">
        What the conductor would change to make the live orchestra match the
        <a href="/sheet" class="text-primary-700 underline">sheet</a> — the same output as
        <code class="font-mono">casals plan</code>. <strong>Plan</strong> reads live IC state
        (10–60 s on a large orchestra); <strong>Apply</strong> executes up to {APPLY_BATCH} items of the
        displayed plan, then re-plans.
      </p>
    </div>
    <div class="flex items-center gap-2 self-start shrink-0">
      <button class="btn-secondary btn-sm" onclick={load} disabled={loading || planning || applying}>Refresh</button>
      <button class="btn-secondary btn-sm" onclick={runPlan} disabled={!$isAuthenticated || planning || applying} title={$isAuthenticated ? '' : 'Log in as a commander to plan'}>
        {#if planning}
          <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
          </svg>
          Planning…
        {:else}
          Plan
        {/if}
      </button>
      <button class="btn-primary btn-sm" onclick={runApply} disabled={!canApply}>
        {#if applying}
          <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
          </svg>
          Applying…
        {:else}
          Apply
        {/if}
      </button>
    </div>
  </div>

  {#if hasDestructive}
    <label class="card border-red-200 bg-red-50 px-4 py-3 flex items-start gap-3 cursor-pointer">
      <input type="checkbox" class="mt-0.5" bind:checked={confirmDestructive} disabled={applying} />
      <span class="text-sm text-red-800">
        <span class="font-semibold">Confirm destructive items.</span>
        This plan deletes, uninstalls or reinstalls something. Apply stays disabled until you confirm.
      </span>
    </label>
  {/if}

  {#if notice}
    <div class="card border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{notice}</div>
  {/if}
  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <svg class="w-5 h-5 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      </svg>
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  {#if loading}
    <div class="card p-4"><div class="skeleton h-16 w-full"></div></div>
  {:else if !plan}
    <div class="card p-8 text-center">
      <p class="text-primary-500 text-sm font-medium">No plan stored yet</p>
      <p class="text-primary-400 text-xs mt-1">
        {$isAuthenticated ? 'Click Plan to compute one from the sheet and live IC state.' : 'Log in as a commander to compute a plan.'}
      </p>
    </div>
  {:else}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 space-y-6">
        {#if plan.items.length === 0}
          <div class="card border-emerald-200 bg-emerald-50 p-5">
            <p class="text-base font-semibold text-emerald-800">✓ Converged — nothing to do</p>
            <p class="text-xs text-emerald-700 mt-1 font-mono">
              plan {shortHash(plan.hash, 16)} · sheet {shortHash(plan.sheet_hash, 16)}
            </p>
          </div>
        {:else}
          <div class="card overflow-hidden">
            <div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--color-border-primary)] bg-primary-50/60">
              <span class="text-sm font-semibold text-primary-900">Items</span>
              <span class="text-xs text-primary-400">{plan.items.length} change(s), applied in order</span>
            </div>
            {@render itemTable(plan.items, 'item')}
          </div>
        {/if}

        <div class="card overflow-hidden">
          {@render section('drift', 'Drift', plan.drift.length)}
          {#if open.drift}
            {#if plan.drift.length === 0}
              <p class="px-4 pb-3 text-xs text-primary-400">No drift — live state matches the sheet for every managed field.</p>
            {:else}
              {@render itemTable(plan.drift, 'drift')}
            {/if}
          {/if}
        </div>

        <div class="card overflow-hidden">
          {@render section('unmanaged', 'Unmanaged canisters', plan.unmanaged.length)}
          {#if open.unmanaged}
            {#if plan.unmanaged.length === 0}
              <p class="px-4 pb-3 text-xs text-primary-400">Every canister the conductor controls is declared in the sheet.</p>
            {:else}
              <ul class="divide-y divide-[var(--color-border-primary)]">
                {#each plan.unmanaged as u (u.canister_id)}
                  <li class="px-4 py-2 text-sm flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                    <span class="font-medium text-primary-900">{u.name || '—'}</span>
                    <span class="font-mono text-xs text-primary-400">{u.canister_id}</span>
                    <span class="text-xs text-primary-500 sm:ml-auto">{u.reason}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          {/if}
        </div>

        <div class="card overflow-hidden">
          {@render section('unverifiable', 'Unverifiable', plan.unverifiable.length)}
          {#if open.unverifiable}
            {#if plan.unverifiable.length === 0}
              <p class="px-4 pb-3 text-xs text-primary-400">Every declared field could be read back from the IC.</p>
            {:else}
              <ul class="divide-y divide-[var(--color-border-primary)]">
                {#each plan.unverifiable as u (`${u.target}|${u.field}`)}
                  <li class="px-4 py-2 text-sm flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                    <span class="font-medium text-primary-900">{u.target}</span>
                    <span class="font-mono text-xs text-primary-500">{u.field}</span>
                    <span class="text-xs text-primary-500 sm:ml-auto">{u.reason}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          {/if}
        </div>

        <div class="card overflow-hidden">
          {@render section('info', 'Info', plan.info.length)}
          {#if open.info}
            {#if plan.info.length === 0}
              <p class="px-4 pb-3 text-xs text-primary-400">No notes.</p>
            {:else}
              <ul class="divide-y divide-[var(--color-border-primary)]">
                {#each plan.info as n, i (`${n.target}|${i}`)}
                  <li class="px-4 py-2 text-sm flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                    <span class="font-medium text-primary-900">{n.target}</span>
                    <span class="text-xs text-primary-500">{n.note}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          {/if}
        </div>
      </div>

      <div class="space-y-6">
        <div class="card p-4">
          <h2 class="text-sm font-semibold text-primary-900 mb-3">Plan</h2>
          <dl class="space-y-1.5 text-xs">
            <div class="flex justify-between gap-3"><dt class="text-primary-500">Plan hash</dt><dd class="font-mono text-primary-800 truncate" title={plan.hash}>{shortHash(plan.hash, 16)}</dd></div>
            <div class="flex justify-between gap-3"><dt class="text-primary-500">Sheet hash</dt><dd class="font-mono text-primary-800 truncate" title={plan.sheet_hash}>{shortHash(plan.sheet_hash, 16)}</dd></div>
            <div class="flex justify-between gap-3"><dt class="text-primary-500">Environment</dt><dd class="font-mono text-primary-800">{plan.env || '—'}</dd></div>
            <div class="flex justify-between gap-3"><dt class="text-primary-500">Computed</dt><dd class="text-primary-800">{fmtNs(plan.created_at_ns) || '—'}</dd></div>
            <div class="border-t border-[var(--color-border-primary)] my-1"></div>
            <div class="flex justify-between"><dt class="text-primary-500">Items</dt><dd class="font-mono text-primary-800">{plan.items.length}</dd></div>
            <div class="flex justify-between"><dt class="text-primary-400">· destructive</dt><dd class="font-mono {hasDestructive ? 'text-red-600' : 'text-primary-500'}">{plan.items.filter((i) => i.destructive).length}</dd></div>
            <div class="flex justify-between"><dt class="text-primary-500">Drift</dt><dd class="font-mono text-primary-800">{plan.drift.length}</dd></div>
            <div class="flex justify-between"><dt class="text-primary-500">Unmanaged</dt><dd class="font-mono text-primary-800">{plan.unmanaged.length}</dd></div>
            <div class="flex justify-between"><dt class="text-primary-500">Unverifiable</dt><dd class="font-mono text-primary-800">{plan.unverifiable.length}</dd></div>
          </dl>
        </div>

        <div class="card p-4">
          <h2 class="text-sm font-semibold text-primary-900 mb-3">Last apply</h2>
          {#if !lastApplied}
            <p class="text-xs text-primary-400">Nothing applied yet.</p>
          {:else}
            <dl class="space-y-1.5 text-xs">
              <div class="flex justify-between gap-3">
                <dt class="text-primary-500">Plan hash</dt>
                <dd class="font-mono text-primary-800 truncate" title={lastApplied.plan_hash}>
                  {shortHash(lastApplied.plan_hash, 16)}{#if plan && lastApplied.plan_hash !== plan.hash}<span class="text-primary-400 font-sans"> (older)</span>{/if}
                </dd>
              </div>
              <div class="flex justify-between"><dt class="text-primary-500">Applied</dt><dd class="font-mono text-emerald-700">{lastApplied.applied.length}</dd></div>
              <div class="flex justify-between"><dt class="text-primary-500">Skipped</dt><dd class="font-mono text-primary-800">{lastApplied.skipped.length}</dd></div>
              <div class="flex justify-between"><dt class="text-primary-500">Remaining</dt><dd class="font-mono text-primary-800">{lastApplied.remaining}</dd></div>
            </dl>
            {#if lastApplied.applied.length}
              <ul class="mt-3 space-y-1 text-xs font-mono text-primary-600">
                {#each lastApplied.applied as a (a.seq)}
                  <li>✓ #{a.seq} {a.kind} <span class="text-primary-400">{targetLabel(a.target)}</span></li>
                {/each}
              </ul>
            {/if}
            {#if lastApplied.failed}
              <div class="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs">
                <p class="font-semibold text-red-700">✗ #{lastApplied.failed.seq} {lastApplied.failed.kind} · {targetLabel(lastApplied.failed.target)}</p>
                <p class="text-red-600 mt-0.5 break-words">{lastApplied.failed.error ?? lastApplied.failed.reason}</p>
              </div>
            {/if}
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>
