<script lang="ts">
  import type { Tree } from '$lib/api';
  import { backendCanisterId, getTree } from '$lib/api';
  import { get } from 'svelte/store';
  import { identity } from '$lib/auth';
  import {
    buildMultisigAction,
    multisigPropose,
    type MultisigActionType,
  } from '$lib/multisigClient';

  interface Props {
    canisterId: string;
    tree?: Tree | null;
    defaultExpirySecs?: number;
    compact?: boolean;
    onsuccess?: () => void;
  }

  let {
    canisterId,
    tree = null,
    defaultExpirySecs = 604800,
    compact = false,
    onsuccess,
  }: Props = $props();

  let open = $state(false);
  let actionType = $state<MultisigActionType>('SetCanisterControllers');
  let busy = $state(false);
  let error = $state('');
  let loadedTree = $state<Tree | null>(null);

  let targetCanister = $state('');
  let controllersText = $state('');
  let addSigners = $state('');
  let removeSigners = $state('');
  let newThreshold = $state('');
  let batonId = $state('');
  let commander = $state('');
  let standName = $state('');

  const canisterOptions = $derived.by(() => {
    const src = loadedTree ?? tree;
    if (!src) return [];
    const out: { id: string; label: string }[] = [];
    for (const sec of src.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (c.canister_id) out.push({ id: c.canister_id, label: c.name });
        }
      }
    }
    return out;
  });

  async function ensureTree() {
    if (loadedTree ?? tree) return loadedTree ?? tree;
    loadedTree = await getTree().catch(() => null);
    return loadedTree;
  }

  function resetFields() {
    error = '';
    targetCanister = canisterOptions[0]?.id ?? '';
    controllersText = '';
    addSigners = '';
    removeSigners = '';
    newThreshold = '';
    batonId = '';
    commander = '';
    standName = '';
  }

  async function toggle() {
    if (busy) return;
    open = !open;
    if (open) {
      await ensureTree();
      resetFields();
    }
  }

  async function submit(event: Event) {
    event.preventDefault();
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
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
        capabilities: 'propose:managed_upgrade',
        policy_json: '{}',
        add_controllers: '',
        remove_controllers: '',
        casals_backend: backendCanisterId(),
        stand: standName,
        canister_id: targetCanister,
      });
      await multisigPropose(canisterId, action, id);
      open = false;
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="relative">
  <button class={compact ? 'btn-ghost btn-sm' : 'btn-primary btn-sm'} type="button" onclick={toggle}>
    {open ? 'Cancel' : 'Propose'}
  </button>

  {#if open}
    <form
      class="absolute right-0 top-full z-20 mt-2 w-[min(100vw-2rem,22rem)] rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg space-y-2"
      onsubmit={submit}
    >
      <label class="label" for="ms-type">Action</label>
      <select id="ms-type" class="input text-sm" bind:value={actionType}>
        <option value="SetCanisterControllers">Set controllers</option>
        <option value="ManageSigners">Manage signers</option>
        <option value="AddCommander">Add baton commander</option>
        <option value="RemoveCommander">Remove baton commander</option>
        <option value="DestroyStand">Destroy stand</option>
        <option value="DestroyCanister">Destroy canister</option>
      </select>

      {#if actionType === 'SetCanisterControllers'}
        <label class="label" for="ms-target">Canister</label>
        {#if canisterOptions.length}
          <select id="ms-target" class="input text-xs font-mono" bind:value={targetCanister}>
            {#each canisterOptions as opt (opt.id)}
              <option value={opt.id}>{opt.label}</option>
            {/each}
          </select>
        {:else}
          <input id="ms-target" class="input text-xs font-mono" bind:value={targetCanister} placeholder="aaaaa-aa" />
        {/if}
        <label class="label" for="ms-ctls">Controllers (one per line)</label>
        <textarea id="ms-ctls" class="input text-xs font-mono min-h-[72px]" bind:value={controllersText}></textarea>
      {:else if actionType === 'ManageSigners'}
        <label class="label" for="ms-add">Add signers</label>
        <textarea id="ms-add" class="input text-xs font-mono min-h-[56px]" bind:value={addSigners}></textarea>
        <label class="label" for="ms-rem">Remove signers</label>
        <textarea id="ms-rem" class="input text-xs font-mono min-h-[56px]" bind:value={removeSigners}></textarea>
        <label class="label" for="ms-th">New threshold</label>
        <input id="ms-th" class="input text-sm" type="number" min="1" bind:value={newThreshold} placeholder="optional" />
      {:else if actionType === 'DestroyStand'}
        <label class="label" for="ms-stand">Stand name</label>
        <input id="ms-stand" class="input text-sm" bind:value={standName} placeholder="stand name" />
      {:else if actionType === 'DestroyCanister'}
        <label class="label" for="ms-destroy-target">Canister</label>
        {#if canisterOptions.length}
          <select id="ms-destroy-target" class="input text-xs font-mono" bind:value={targetCanister}>
            {#each canisterOptions as opt (opt.id)}
              <option value={opt.id}>{opt.label}</option>
            {/each}
          </select>
        {:else}
          <input id="ms-destroy-target" class="input text-xs font-mono" bind:value={targetCanister} placeholder="aaaaa-aa" />
        {/if}
      {:else}
        <label class="label" for="ms-baton">Baton id</label>
        <input id="ms-baton" class="input text-xs font-mono" bind:value={batonId} placeholder="aaaaa-aa" />
        <label class="label" for="ms-cmd">Commander</label>
        <input id="ms-cmd" class="input text-xs font-mono" bind:value={commander} placeholder="aaaaa-aa" />
      {/if}

      {#if error}
        <p class="text-xs text-red-700">{error}</p>
      {/if}

      <button type="submit" class="btn-primary btn-sm w-full" disabled={busy}>
        {busy ? 'Submitting…' : 'Submit'}
      </button>
    </form>
  {/if}
</div>
