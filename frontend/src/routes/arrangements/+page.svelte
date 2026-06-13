<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listArrangements,
    getArrangement,
    setArrangement,
    setActiveArrangement,
    deleteArrangement,
    applyArrangementAll,
  } from '$lib/api';
  import type {
    Arrangement,
    ArrangementSummary,
    ArrangementApplyProgress,
  } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  let summaries = $state<ArrangementSummary[]>([]);
  let selected = $state<string | null>(null);
  let text = $state('');
  let loading = $state(true);
  let error = $state('');
  let busy = $state(false);
  let applying = $state(false);
  let applyProgress = $state<ArrangementApplyProgress | null>(null);
  let lastApply = $state<{ applied: number; failed: number; steps_total: number | null } | null>(null);

  type ParsedDoc = {
    name: string;
    description: string;
    active: boolean;
    parameters: Record<string, unknown>;
    steps: Arrangement['steps'];
  };

  let parsed = $derived.by<{ doc: ParsedDoc | null; err: string }>(() => {
    if (!text.trim()) return { doc: null, err: 'Arrangement is empty' };
    try {
      const obj = JSON.parse(text);
      if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) {
        return { doc: null, err: 'Arrangement must be a JSON object' };
      }
      const name = String(obj.name ?? '').trim();
      if (!name) return { doc: null, err: 'Arrangement must have a non-empty "name"' };
      if (!Array.isArray(obj.steps)) return { doc: null, err: 'Arrangement must have a "steps" array' };
      const parameters = obj.parameters;
      if (parameters !== undefined && (typeof parameters !== 'object' || parameters === null || Array.isArray(parameters))) {
        return { doc: null, err: '"parameters" must be a JSON object' };
      }
      for (const step of obj.steps) {
        if (!step || typeof step !== 'object' || Array.isArray(step)) {
          return { doc: null, err: 'Each step must be an object' };
        }
        if (!String(step.target ?? '').trim() || !String(step.method ?? '').trim()) {
          return { doc: null, err: 'Each step needs "target" and "method"' };
        }
      }
      return {
        doc: {
          name,
          description: String(obj.description ?? ''),
          active: !!obj.active,
          parameters: (parameters ?? {}) as Record<string, unknown>,
          steps: obj.steps,
        },
        err: '',
      };
    } catch (e: any) {
      return { doc: null, err: e?.message ?? 'Invalid JSON' };
    }
  });

  let stepPreview = $derived(parsed.doc?.steps.slice(0, 12) ?? []);

  function docToText(doc: Arrangement): string {
    return JSON.stringify(
      {
        name: doc.name,
        description: doc.description,
        active: doc.active,
        parameters: doc.parameters,
        steps: doc.steps,
      },
      null,
      2,
    );
  }

  async function loadList(selectName?: string | null) {
    loading = true;
    error = '';
    try {
      summaries = await listArrangements();
      const pick =
        selectName ??
        selected ??
        summaries.find((s) => s.active)?.name ??
        summaries[0]?.name ??
        null;
      selected = pick;
      if (pick) await loadOne(pick);
      else {
        text = '';
        lastApply = null;
      }
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  async function loadOne(name: string) {
    try {
      const arr = await getArrangement(name);
      selected = arr.name;
      text = docToText(arr);
      lastApply = null;
    } catch (e: any) {
      toasts.error(e?.message ?? `Could not load arrangement "${name}"`);
    }
  }

  onMount(() => loadList());

  async function selectName(name: string) {
    if (name === selected || busy || applying) return;
    await loadOne(name);
  }

  async function save() {
    if (!parsed.doc) {
      toasts.error(parsed.err);
      return;
    }
    busy = true;
    try {
      await setArrangement(parsed.doc);
      toasts.success(`Saved arrangement "${parsed.doc.name}"`);
      await loadList(parsed.doc.name);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save arrangement');
    } finally {
      busy = false;
    }
  }

  async function activate() {
    if (!parsed.doc) {
      toasts.error(parsed.err);
      return;
    }
    busy = true;
    try {
      await setActiveArrangement(parsed.doc.name);
      toasts.success(`Activated "${parsed.doc.name}"`);
      await loadList(parsed.doc.name);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to activate arrangement');
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (!parsed.doc) return;
    if (!confirm(`Delete arrangement "${parsed.doc.name}"? This cannot be undone.`)) return;
    busy = true;
    try {
      await deleteArrangement(parsed.doc.name);
      toasts.success(`Deleted "${parsed.doc.name}"`);
      selected = null;
      await loadList();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to delete arrangement');
    } finally {
      busy = false;
    }
  }

  async function apply() {
    if (!parsed.doc) {
      toasts.error(parsed.err);
      return;
    }
    const steps = parsed.doc.steps.length;
    if (
      steps > 0 &&
      !confirm(
        `Apply "${parsed.doc.name}" (${steps} step${steps === 1 ? '' : 's'})? ` +
          'This runs post-deploy calls against managed canisters.',
      )
    ) {
      return;
    }
    applying = true;
    applyProgress = null;
    lastApply = null;
    try {
      const result = await applyArrangementAll({
        name: parsed.doc.name,
        onProgress: (p) => {
          applyProgress = p;
        },
      });
      lastApply = result;
      if (result.failed > 0) {
        toasts.error(`Applied with ${result.failed} failed step(s)`);
      } else {
        toasts.success(`Applied ${result.applied} step(s)`);
      }
    } catch (e: any) {
      toasts.error(e?.message ?? 'Apply failed');
    } finally {
      applying = false;
      applyProgress = null;
    }
  }
</script>

<svelte:head><title>Casals · Arrangements</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Arrangements</h1>
      <p class="text-sm text-primary-500 mt-1 max-w-2xl">
        Post-deploy environment configuration: a flat <strong>parameters</strong> map plus
        ordered <strong>steps</strong> ({'{'}target, method, args{'}'}) Casals runs against
        managed canisters after a sheet deploy. Exactly one arrangement is
        <strong>active</strong> per instance.
      </p>
      <p class="text-xs text-primary-400 mt-1 max-w-2xl">
        A sheet stands up code; an arrangement configures state (runtime flags, registry
        installs, branding, etc.). Casals stores and forwards the data without interpreting it.
      </p>
    </div>
    <div class="flex items-center gap-2 self-start shrink-0 flex-wrap">
      <button class="btn-secondary btn-sm" onclick={() => loadList(selected)} disabled={loading || busy || applying}>
        Refresh
      </button>
      {#if $isAuthenticated}
        <button class="btn-secondary btn-sm" onclick={save} disabled={busy || applying || !parsed.doc}>Save</button>
        <button class="btn-secondary btn-sm" onclick={activate} disabled={busy || applying || !parsed.doc}>
          Activate
        </button>
        <button class="btn-primary btn-sm" onclick={apply} disabled={busy || applying || !parsed.doc}>
          {#if applying}
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            Applying…
          {:else}
            Apply steps
          {/if}
        </button>
        <button class="btn-ghost btn-sm text-red-600 hover:text-red-800" onclick={remove} disabled={busy || applying || !parsed.doc}>
          Delete
        </button>
      {/if}
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

  {#if loading}
    <div class="skeleton h-10 w-full rounded-lg"></div>
    <div class="skeleton h-96 w-full rounded-lg"></div>
  {:else if summaries.length === 0}
    <div class="card p-8 text-center">
      <p class="text-sm text-primary-500">No arrangements stored on this Casals instance yet.</p>
      <p class="text-xs text-primary-400 mt-2 max-w-md mx-auto">
        Seed one from JSON (e.g. <code class="font-mono">casals-config/arrangements/test.json</code>)
        via <code class="font-mono">seed.py --arrangement …</code> or paste JSON below and Save when logged in.
      </p>
      {#if $isAuthenticated}
        <textarea
          bind:value={text}
          placeholder={'{\n  "name": "my-env",\n  "description": "…",\n  "active": true,\n  "parameters": {},\n  "steps": []\n}'}
          spellcheck="false"
          class="mt-4 w-full max-w-xl mx-auto h-48 font-mono text-xs p-3 rounded-lg border border-[var(--color-border-primary)]"
        ></textarea>
        {#if parsed.err && text.trim()}
          <p class="text-xs text-red-600 mt-2">⚠ {parsed.err}</p>
        {/if}
        <button class="btn-primary btn-sm mt-3" onclick={save} disabled={busy || !parsed.doc}>Save first arrangement</button>
      {/if}
    </div>
  {:else}
    <div class="flex flex-wrap gap-2">
      {#each summaries as s (s.name)}
        <button
          type="button"
          class="px-3 py-2 rounded-lg border text-left text-sm transition-colors min-w-[8rem]
            {selected === s.name
              ? 'border-primary-400 bg-primary-50 text-primary-900'
              : 'border-[var(--color-border-primary)] bg-white text-primary-600 hover:bg-primary-50'}"
          onclick={() => selectName(s.name)}
          disabled={busy || applying}
        >
          <span class="font-medium">{s.name}</span>
          {#if s.active}
            <span class="ml-1.5 text-[10px] uppercase tracking-wide font-semibold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">active</span>
          {/if}
          <span class="block text-[11px] text-primary-400 mt-0.5">{s.step_count} steps · {s.parameter_count} params</span>
        </button>
      {/each}
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 space-y-2">
        <div class="flex items-center justify-between">
          <span class="text-xs font-semibold text-primary-500 uppercase tracking-wider">Arrangement (JSON)</span>
          {#if parsed.doc}
            <span class="text-xs text-primary-400">
              {parsed.doc.steps.length} step(s) · {Object.keys(parsed.doc.parameters).length} parameter(s)
              {#if parsed.doc.active}· active{/if}
            </span>
          {/if}
        </div>
        <textarea
          bind:value={text}
          spellcheck="false"
          readonly={!$isAuthenticated}
          class="w-full h-[28rem] font-mono text-xs leading-relaxed p-4 rounded-lg border border-[var(--color-border-primary)] bg-white text-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-300 resize-y"
        ></textarea>
        {#if parsed.err}
          <p class="text-xs text-red-600">⚠ {parsed.err}</p>
        {:else if parsed.doc}
          <p class="text-xs text-emerald-600">✓ valid arrangement</p>
        {/if}
        {#if !$isAuthenticated}
          <p class="text-xs text-primary-400">Log in as a controller to edit, save, activate, or apply.</p>
        {/if}
      </div>

      <div class="space-y-4">
        {#if applying && applyProgress}
          <div class="card p-4" aria-live="polite" aria-busy="true">
            <h2 class="text-sm font-semibold text-primary-900 mb-2">Applying…</h2>
            <p class="text-xs text-primary-500">
              Step {Math.min(applyProgress.offset + 1, applyProgress.stepsTotal || '?')}
              {#if applyProgress.stepsTotal}
                of {applyProgress.stepsTotal}
              {/if}
              · {applyProgress.applied} ok · {applyProgress.failed} failed
            </p>
            <div class="mt-2 h-1.5 rounded-full bg-primary-100 overflow-hidden">
              {#if applyProgress.stepsTotal > 0}
                <div
                  class="h-full bg-primary-600 transition-all duration-300"
                  style="width: {Math.min(100, (applyProgress.offset / applyProgress.stepsTotal) * 100)}%"
                ></div>
              {/if}
            </div>
          </div>
        {/if}

        {#if lastApply}
          <div class="card p-4">
            <h2 class="text-sm font-semibold text-primary-900 mb-2">Last apply</h2>
            <dl class="text-xs space-y-1">
              <div class="flex justify-between gap-2">
                <dt class="text-primary-400">Applied</dt>
                <dd class="font-mono text-emerald-700">{lastApply.applied}</dd>
              </div>
              <div class="flex justify-between gap-2">
                <dt class="text-primary-400">Failed</dt>
                <dd class="font-mono {lastApply.failed ? 'text-red-600' : 'text-primary-700'}">{lastApply.failed}</dd>
              </div>
              {#if lastApply.steps_total != null}
                <div class="flex justify-between gap-2">
                  <dt class="text-primary-400">Total steps</dt>
                  <dd class="font-mono text-primary-700">{lastApply.steps_total}</dd>
                </div>
              {/if}
            </dl>
          </div>
        {/if}

        <div class="card p-4">
          <h2 class="text-sm font-semibold text-primary-900 mb-2">Step preview</h2>
          {#if stepPreview.length === 0}
            <p class="text-xs text-primary-400">No steps in this arrangement.</p>
          {:else}
            <ol class="text-xs space-y-2 list-decimal list-inside text-primary-600">
              {#each stepPreview as step, i (i)}
                <li class="break-all">
                  <span class="font-mono text-primary-800">{step.method}</span>
                  <span class="text-primary-400"> → </span>
                  <span class="font-mono">{step.target}</span>
                </li>
              {/each}
            </ol>
            {#if (parsed.doc?.steps.length ?? 0) > stepPreview.length}
              <p class="text-[11px] text-primary-400 mt-2">
                + {(parsed.doc?.steps.length ?? 0) - stepPreview.length} more in JSON
              </p>
            {/if}
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>
