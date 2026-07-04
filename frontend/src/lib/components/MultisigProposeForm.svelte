<script lang="ts">
  import type { Tree } from '$lib/api';
  import { get } from 'svelte/store';
  import { identity } from '$lib/auth';
  import {
    buildMultisigAction,
    multisigPropose,
    type MultisigActionType,
  } from '$lib/multisigClient';
  import { resolveWasmType } from '$lib/canisterTypes';

  interface Props {
    canisterId: string;
    tree?: Tree | null;
    defaultExpirySecs?: number;
    onsuccess?: () => void;
  }

  let { canisterId, tree = null, defaultExpirySecs = 604800, onsuccess }: Props = $props();

  let open = $state(false);
  let actionType = $state<MultisigActionType>('ManageSigners');
  let busy = $state(false);
  let error = $state('');

  let addSigners = $state('');
  let removeSigners = $state('');
  let newThreshold = $state('');
  let targetCanister = $state('');
  let controllersText = $state('');
  let batonId = $state('');
  let commander = $state('');
  let capabilities = $state('');
  let policyJson = $state('');
  let addControllers = $state('');
  let removeControllers = $state('');
  let proposalExpiryDays = $state('');

  const defaultExpiryDays = $derived(Math.max(1, Math.round(defaultExpirySecs / 86400)));

  const batonOptions = $derived.by(() => {
    if (!tree) return [];
    const out: { id: string; label: string }[] = [];
    for (const sec of tree.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (resolveWasmType(c) === 'baton' && c.canister_id) {
            out.push({ id: c.canister_id, label: `${c.name} (${c.canister_id.slice(0, 5)}…)` });
          }
        }
      }
    }
    return out;
  });

  const canisterOptions = $derived.by(() => {
    if (!tree) return [];
    const out: { id: string; label: string }[] = [];
    for (const sec of tree.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (c.canister_id) {
            out.push({ id: c.canister_id, label: `${c.name} (${c.canister_id.slice(0, 5)}…)` });
          }
        }
      }
    }
    return out;
  });

  $effect(() => {
    if (!open || batonId) return;
    if (batonOptions.length) batonId = batonOptions[0].id;
  });

  function resetFields() {
    error = '';
    addSigners = '';
    removeSigners = '';
    newThreshold = '';
    targetCanister = canisterOptions[0]?.id ?? '';
    controllersText = '';
    batonId = batonOptions[0]?.id ?? '';
    commander = '';
    capabilities = 'canister.upgrade, canister.stop, canister.start';
    policyJson = '{}';
    addControllers = '';
    removeControllers = '';
    proposalExpiryDays = '';
  }

  function toggle() {
    if (busy) return;
    open = !open;
    if (open) resetFields();
  }

  async function submit(event: Event) {
    event.preventDefault();
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required to propose';
      return;
    }
    busy = true;
    try {
      const action = buildMultisigAction(actionType, {
        add_signers: addSigners,
        remove_signers: removeSigners,
        new_threshold: newThreshold,
        target_canister: targetCanister,
        controllers: controllersText,
        baton_id: batonId,
        commander,
        capabilities,
        policy_json: policyJson,
        add_controllers: addControllers,
        remove_controllers: removeControllers,
      });
      const expiryRaw = String(proposalExpiryDays ?? '').trim();
      const expirySecs = expiryRaw
        ? Math.max(1, parseInt(expiryRaw, 10) || 0) * 86400
        : null;
      await multisigPropose(canisterId, action, id, expirySecs);
      open = false;
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="space-y-3">
  <button class="btn-primary btn-sm" type="button" onclick={toggle}>
    {open ? 'Cancel proposal' : 'New proposal'}
  </button>

  {#if open}
    <form class="rounded-lg border border-primary-200 bg-primary-50/40 p-4 space-y-3" onsubmit={submit}>
      <div>
        <label class="label" for="ms-action">Action type</label>
        <select id="ms-action" class="input" bind:value={actionType}>
          <option value="ManageSigners">Manage signers</option>
          <option value="SetCanisterControllers">Set canister controllers</option>
          <option value="AddCommander">Add Baton commander</option>
          <option value="RemoveCommander">Remove Baton commander</option>
          <option value="UpdateBatonSettings">Update Baton settings</option>
          <option value="SetPolicy">Set Baton policy</option>
        </select>
      </div>

      <div>
        <label class="label" for="ms-expiry">Proposal expiry (days)</label>
        <input
          id="ms-expiry"
          class="input"
          type="number"
          min="1"
          bind:value={proposalExpiryDays}
          placeholder="Default: {defaultExpiryDays} days"
        />
        <p class="text-xs text-primary-400 mt-1">
          Leave empty to use the canister default ({defaultExpiryDays} days).
          Each proposal gets its own deadline from submit time.
        </p>
      </div>

      {#if actionType === 'ManageSigners'}
        <div>
          <label class="label" for="ms-add">Add signers</label>
          <textarea id="ms-add" class="input font-mono text-xs min-h-[72px]" bind:value={addSigners} placeholder="One principal per line"></textarea>
        </div>
        <div>
          <label class="label" for="ms-remove">Remove signers</label>
          <textarea id="ms-remove" class="input font-mono text-xs min-h-[72px]" bind:value={removeSigners} placeholder="One principal per line"></textarea>
        </div>
        <div>
          <label class="label" for="ms-threshold">New threshold (optional)</label>
          <input id="ms-threshold" class="input" type="number" min="1" bind:value={newThreshold} placeholder="Leave empty to keep current" />
        </div>
      {:else if actionType === 'SetCanisterControllers'}
        <div>
          <label class="label" for="ms-target">Target canister</label>
          {#if canisterOptions.length}
            <select id="ms-target" class="input font-mono text-xs" bind:value={targetCanister}>
              {#each canisterOptions as opt (opt.id)}
                <option value={opt.id}>{opt.label}</option>
              {/each}
            </select>
          {:else}
            <input id="ms-target" class="input font-mono text-xs" bind:value={targetCanister} placeholder="aaaaa-aa" />
          {/if}
        </div>
        <div>
          <label class="label" for="ms-ctls">Controllers (full replacement list)</label>
          <textarea id="ms-ctls" class="input font-mono text-xs min-h-[88px]" bind:value={controllersText} placeholder="One principal per line"></textarea>
        </div>
      {:else if actionType === 'AddCommander' || actionType === 'RemoveCommander' || actionType === 'UpdateBatonSettings' || actionType === 'SetPolicy'}
        <div>
          <label class="label" for="ms-baton">Baton canister</label>
          {#if batonOptions.length}
            <select id="ms-baton" class="input font-mono text-xs" bind:value={batonId}>
              {#each batonOptions as opt (opt.id)}
                <option value={opt.id}>{opt.label}</option>
              {/each}
            </select>
          {:else}
            <input id="ms-baton" class="input font-mono text-xs" bind:value={batonId} placeholder="aaaaa-aa" />
          {/if}
        </div>
        {#if actionType === 'AddCommander' || actionType === 'RemoveCommander'}
          <div>
            <label class="label" for="ms-commander">Commander principal</label>
            <input id="ms-commander" class="input font-mono text-xs" bind:value={commander} placeholder="aaaaa-aa" />
          </div>
        {/if}
        {#if actionType === 'AddCommander'}
          <div>
            <label class="label" for="ms-caps">Capabilities</label>
            <textarea id="ms-caps" class="input font-mono text-xs min-h-[64px]" bind:value={capabilities} placeholder="canister.upgrade, canister.stop"></textarea>
          </div>
        {/if}
        {#if actionType === 'UpdateBatonSettings'}
          <div>
            <label class="label" for="ms-add-ctl">Add controllers</label>
            <textarea id="ms-add-ctl" class="input font-mono text-xs min-h-[64px]" bind:value={addControllers} placeholder="One principal per line"></textarea>
          </div>
          <div>
            <label class="label" for="ms-rem-ctl">Remove controllers</label>
            <textarea id="ms-rem-ctl" class="input font-mono text-xs min-h-[64px]" bind:value={removeControllers} placeholder="One principal per line"></textarea>
          </div>
        {/if}
        {#if actionType === 'SetPolicy'}
          <div>
            <label class="label" for="ms-policy">Policy JSON</label>
            <textarea id="ms-policy" class="input font-mono text-xs min-h-[96px]" bind:value={policyJson}></textarea>
          </div>
        {/if}
      {/if}

      {#if error}
        <p class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>
      {/if}

      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary btn-sm" onclick={toggle} disabled={busy}>Cancel</button>
        <button type="submit" class="btn-primary btn-sm" disabled={busy}>
          {busy ? 'Submitting…' : 'Submit proposal'}
        </button>
      </div>
    </form>
  {/if}
</div>
