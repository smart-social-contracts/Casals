<script lang="ts">
  import {
    batonProposeRelease,
    standReleasePreview,
    wasmList,
    type CanisterRow,
    type StandReleasePreview,
    type StandReleaseRequest,
    type Wasm,
  } from '$lib/api';
  import { wasmsForCanister } from '$lib/batonUpgrade';
  import { knownContentNamespaces } from '$lib/contentDeploy';
  import { localToNs, roleOf } from '$lib/standRelease';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    stand: string;
    /** The stand's canisters other than its baton. */
    members: CanisterRow[];
    allCanisters: CanisterRow[];
    current?: string;
    busyElsewhere?: boolean;
    onsuccess?: (actionId: string) => void | Promise<void>;
  }

  let { stand, members, allCanisters, current = '', busyElsewhere = false, onsuccess }: Props = $props();

  let open = $state(false);
  let busy = $state(false);
  let error = $state('');
  let catalog = $state<Wasm[]>([]);
  let label = $state('');
  let wasmByRole = $state<Record<string, string>>({});
  let contentByRole = $state<Record<string, string>>({});
  let message = $state('');
  let title = $state('');
  let duration = $state(10);
  let notBefore = $state('');
  let bake = $state(0);
  let preview = $state<StandReleasePreview | null>(null);

  const rows = $derived(members.map((c) => ({ c, role: roleOf(c) })));
  const namespaces = $derived(knownContentNamespaces(allCanisters));

  async function start() {
    open = true;
    error = '';
    preview = null;
    label = `${stand}@${new Date().toISOString().slice(0, 10)}`;
    wasmByRole = Object.fromEntries(rows.map(({ c, role }) => [role, c.wasm]));
    contentByRole = Object.fromEntries(rows.filter(({ c }) => c.kind === 'frontend').map(({ c, role }) => [role, c.content]));
    try {
      catalog = await wasmList();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  function request(): StandReleaseRequest {
    const spec: StandReleaseRequest['members'] = {};
    for (const { c, role } of rows) {
      const m: { wasm?: string; content?: string } = {};
      if (wasmByRole[role] && wasmByRole[role] !== c.wasm) m.wasm = wasmByRole[role];
      if (c.kind === 'frontend' && contentByRole[role]?.trim() && contentByRole[role].trim() !== c.content) {
        m.content = contentByRole[role].trim();
      }
      if (m.wasm || m.content) spec[role] = m;
    }
    const req: StandReleaseRequest = { stand, release: { label: label.trim() || `${stand}@release` }, members: spec };
    if (members.some((c) => c.kind === 'frontend')) {
      req.maintenance = {
        ...(message.trim() ? { message: message.trim() } : {}),
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(duration > 0 ? { duration_seconds: Math.round(duration * 60) } : {}),
      };
    }
    const when = localToNs(notBefore);
    if (when) req.not_before = when;
    if (bake > 0) req.bake_window_seconds = Math.round(bake);
    return req;
  }

  async function runPreview() {
    busy = true;
    error = '';
    try {
      preview = await standReleasePreview(request());
    } catch (e: unknown) {
      preview = null;
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function propose() {
    busy = true;
    error = '';
    try {
      const res = await batonProposeRelease(request());
      toasts.success(`Release proposed: ${res.steps} steps for ${res.members.length} members`);
      open = false;
      await onsuccess?.(res.action_id);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="rounded-lg border border-[var(--color-border-primary)] p-4 space-y-3">
  <div class="flex flex-wrap items-start justify-between gap-2">
    <div class="space-y-0.5">
      <h3 class="text-sm font-medium text-primary-900">Release the whole stand</h3>
      <p class="text-xs text-primary-500 max-w-xl">
        Every member moves together or none does. Frontends show the Casals maintenance page while the backends
        change; a failed check rolls the stand back to its snapshots.
        {#if current}Now on <span class="font-mono">{current}</span>.{/if}
      </p>
    </div>
    {#if !open}
      <button class="btn-primary btn-sm" type="button" disabled={busyElsewhere} onclick={start}
              title={busyElsewhere ? 'Another action is in progress on this Baton' : ''}>Upgrade stand…</button>
    {/if}
  </div>

  {#if open}
    <label class="block text-xs text-primary-600 space-y-1">
      <span>Release label</span>
      <input class="input w-full font-mono text-xs" bind:value={label} />
    </label>

    <div class="overflow-x-auto">
      <table class="w-full text-xs">
        <thead class="text-primary-400 text-left">
          <tr><th class="py-1 pr-2">Role</th><th class="py-1 pr-2">Canister</th><th class="py-1 pr-2">WASM</th><th class="py-1">Content</th></tr>
        </thead>
        <tbody class="divide-y divide-[var(--color-border-primary)]">
          {#each rows as { c, role } (c.canister_id)}
            <tr>
              <td class="py-1.5 pr-2 font-mono">{role}</td>
              <td class="py-1.5 pr-2">{c.name} <span class="text-primary-400">{c.kind}</span></td>
              <td class="py-1.5 pr-2">
                <select class="input text-xs font-mono" bind:value={wasmByRole[role]}>
                  <option value={c.wasm}>{c.wasm} (unchanged)</option>
                  {#each wasmsForCanister(c, catalog).filter((w) => w.key !== c.wasm) as w (w.key)}
                    <option value={w.key}>{w.key}</option>
                  {/each}
                </select>
              </td>
              <td class="py-1.5">
                {#if c.kind === 'frontend'}
                  <input class="input text-xs font-mono w-full" list="stand-release-namespaces" bind:value={contentByRole[role]} />
                {:else}
                  <span class="text-primary-300">—</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <datalist id="stand-release-namespaces">
        {#each namespaces as ns (ns)}<option value={ns}></option>{/each}
      </datalist>
    </div>

    {#if members.some((c) => c.kind === 'frontend')}
      <div class="grid sm:grid-cols-[1fr_1fr_8rem] gap-2">
        <label class="text-xs text-primary-600 space-y-1">
          <span>Maintenance heading</span>
          <input class="input w-full text-xs" bind:value={title} placeholder="Down for maintenance" />
        </label>
        <label class="text-xs text-primary-600 space-y-1">
          <span>Maintenance message</span>
          <input class="input w-full text-xs" bind:value={message} placeholder="We are upgrading. Back in a few minutes." />
        </label>
        <label class="text-xs text-primary-600 space-y-1">
          <span>Expected minutes</span>
          <input class="input w-full text-xs" type="number" min="0" bind:value={duration} />
        </label>
      </div>
    {/if}
    <div class="grid sm:grid-cols-2 gap-2">
      <label class="text-xs text-primary-600 space-y-1">
        <span>Not before (optional)</span>
        <input class="input w-full text-xs" type="datetime-local" bind:value={notBefore} />
      </label>
      <label class="text-xs text-primary-600 space-y-1">
        <span>Bake seconds (keep snapshots; an abort meanwhile rolls back)</span>
        <input class="input w-full text-xs" type="number" min="0" bind:value={bake} />
      </label>
    </div>

    {#if preview}
      <ul class="rounded border border-[var(--color-border-primary)] divide-y divide-[var(--color-border-primary)] text-xs">
        {#each preview.members as m (m.canister_id)}
          <li class="px-3 py-1.5 flex flex-wrap gap-2">
            <span class="font-mono w-24 shrink-0">{m.role}</span>
            {#if m.change === 'unchanged'}
              <span class="text-primary-400">unchanged — stopped, snapshotted and started with the rest</span>
            {:else}
              {#if m.wasm}<span class="font-mono">{m.wasm.from} → {m.wasm.to}</span>{/if}
              {#if m.content}<span class="font-mono">{m.content.from || '—'} → {m.content.to}</span>{/if}
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    {#if error}<p class="text-xs text-red-700 whitespace-pre-wrap">{error}</p>{/if}

    <div class="flex flex-wrap gap-2">
      <button class="btn-ghost btn-sm" type="button" disabled={busy} onclick={runPreview}>Preview</button>
      <button class="btn-primary btn-sm" type="button" disabled={busy || !preview} onclick={propose}
              title={preview ? '' : 'Preview first'}>{busy ? 'Working…' : 'Propose release'}</button>
      <button class="btn-ghost btn-sm" type="button" disabled={busy} onclick={() => (open = false)}>Cancel</button>
    </div>
  {/if}
</div>
