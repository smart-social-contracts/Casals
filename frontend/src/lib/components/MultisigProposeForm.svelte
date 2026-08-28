<script lang="ts">
  import type { Tree } from '$lib/api';
  import { backendCanisterId, getTree } from '$lib/api';
  import { get } from 'svelte/store';
  import { identity } from '$lib/auth';
  import { resolveCanisterControllers } from '$lib/controllerAccess';
  import {
    buildMultisigAction,
    multisigPropose,
    type MultisigActionType,
  } from '$lib/multisigClient';
  import { batonForStand } from '$lib/orchestraGovernance';
  import { findStandForCanister } from '$lib/orchestrationNav';

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
  let destroyIdsText = $state('');

  let liveControllers = $state<string[]>([]);
  let controllersLoading = $state(false);
  let controllersError = $state('');
  let knownControllersText = $state('');

  const isControllerAction = $derived(
    actionType === 'SetCanisterControllers' ||
      actionType === 'AddCanisterControllers' ||
      actionType === 'RemoveCanisterControllers',
  );

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

  const cachedControllers = $derived.by(() => {
    const src = loadedTree ?? tree;
    if (!src || !targetCanister) return [];
    for (const sec of src.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (c.canister_id === targetCanister) {
            return c.controllers ?? [];
          }
        }
      }
    }
    return [];
  });

  const currentControllers = $derived.by(() => {
    if (cachedControllers.length) return cachedControllers;
    if (liveControllers.length) return liveControllers;
    return knownControllersText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  });

  const showKnownControllersInput = $derived(
    (actionType === 'AddCanisterControllers' ||
      actionType === 'RemoveCanisterControllers') &&
      (controllersError ||
        (!controllersLoading &&
          !cachedControllers.length &&
          !liveControllers.length)),
  );

  const submitDisabled = $derived(
    busy ||
      (controllersLoading &&
        (actionType === 'AddCanisterControllers' ||
          actionType === 'RemoveCanisterControllers')),
  );

  function patchTreeControllers(canisterId: string, controllers: string[]) {
    if (!loadedTree) return;
    loadedTree = {
      ...loadedTree,
      sections: loadedTree.sections.map((sec) => ({
        ...sec,
        stands: sec.stands.map((stand) => ({
          ...stand,
          canisters: stand.canisters.map((c) =>
            c.canister_id === canisterId ? { ...c, controllers } : c,
          ),
        })),
      })),
    };
  }

  function clearControllerFetchState() {
    liveControllers = [];
    controllersLoading = false;
    controllersError = '';
    knownControllersText = '';
  }

  async function loadLiveControllers() {
    if (!isControllerAction || !targetCanister) {
      clearControllerFetchState();
      return;
    }

    if (cachedControllers.length) {
      clearControllerFetchState();
      return;
    }

    controllersLoading = true;
    controllersError = '';
    liveControllers = [];

    try {
      const list = await resolveCanisterControllers(targetCanister, get(identity));
      liveControllers = list;
      if (list.length) {
        patchTreeControllers(targetCanister, list);
      }
    } catch (e: unknown) {
      controllersError = e instanceof Error ? e.message : String(e);
      liveControllers = [];
    } finally {
      controllersLoading = false;
    }
  }

  function syncControllersTextForAction() {
    if (actionType === 'SetCanisterControllers') {
      controllersText = currentControllers.join('\n');
    } else if (
      actionType === 'AddCanisterControllers' ||
      actionType === 'RemoveCanisterControllers'
    ) {
      controllersText = '';
    }
  }

  async function onActionTypeChange() {
    syncControllersTextForAction();
    if (isControllerAction) {
      await loadLiveControllers();
    } else {
      clearControllerFetchState();
    }
  }

  async function onCanisterChange() {
    syncControllersTextForAction();
    await loadLiveControllers();
  }

  async function ensureTree() {
    if (loadedTree ?? tree) return loadedTree ?? tree;
    loadedTree = await getTree().catch(() => null);
    return loadedTree;
  }

  function multisigControlsTarget(controllers: string[]): boolean {
    return controllers.some((c) => c.toLowerCase() === canisterId.toLowerCase());
  }

  function multisigNotControllerError(controllers: string[]): Error {
    const treeSrc = loadedTree ?? tree;
    const standLoc = findStandForCanister(treeSrc, targetCanister);
    const standBaton = standLoc ? batonForStand(treeSrc, standLoc.stand) : null;
    const controllerList = controllers.length ? controllers.join(', ') : 'unknown';

    let message =
      'This multisig is not an IC controller of the target, so `update_settings` will fail. ' +
      `Current controllers: ${controllerList}.`;

    if (standBaton?.canister_id) {
      const batonLabel = standBaton.name
        ? `${standBaton.canister_id} (${standBaton.name})`
        : standBaton.canister_id;
      message +=
        ` After baton hand-off, this stand is controlled by baton ${batonLabel}.` +
        ' Add this multisig via an existing controller first, or use that controller directly.';
    }

    return new Error(message);
  }

  async function resolveControllersForSubmit(identity: NonNullable<ReturnType<typeof get>>) {
    let controllers = currentControllers;
    if (!controllers.length) {
      const list = await resolveCanisterControllers(targetCanister, identity);
      liveControllers = list;
      controllers = list;
      if (list.length) {
        patchTreeControllers(targetCanister, list);
      }
    }
    return controllers;
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
    destroyIdsText = '';
    clearControllerFetchState();
    syncControllersTextForAction();
  }

  async function toggle() {
    if (busy) return;
    open = !open;
    if (open) {
      await ensureTree();
      resetFields();
      await loadLiveControllers();
    } else {
      clearControllerFetchState();
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
      let controllers = currentControllers;
      if (isControllerAction) {
        controllers = await resolveControllersForSubmit(id);
        if (!multisigControlsTarget(controllers)) {
          throw multisigNotControllerError(controllers);
        }
      }

      const action = buildMultisigAction(actionType, {
        add_signers: addSigners,
        remove_signers: removeSigners,
        new_threshold: newThreshold,
        target_canister: targetCanister,
        controllers: controllersText,
        current_controllers: controllers.join('\n'),
        baton_id: batonId,
        commander,
        capabilities: 'propose:managed_upgrade',
        policy_json: '{}',
        add_controllers: '',
        remove_controllers: '',
        casals_backend: backendCanisterId(),
        stand: standName,
        canister_id: targetCanister,
        canister_ids: destroyIdsText || targetCanister,
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
      <select
        id="ms-type"
        class="input text-sm"
        bind:value={actionType}
        onchange={onActionTypeChange}
      >
        <option value="SetCanisterControllers">Set controllers</option>
        <option value="AddCanisterControllers">Add controllers</option>
        <option value="RemoveCanisterControllers">Remove controllers</option>
        <option value="ManageSigners">Manage signers</option>
        <option value="AddCommander">Add baton commander</option>
        <option value="RemoveCommander">Remove baton commander</option>
        <option value="DestroyStand">Destroy stand</option>
        <option value="DestroyCanister">Destroy canister</option>
        <option value="DestroyCanisters">Destroy canisters (batch)</option>
      </select>

      {#if isControllerAction}
        <label class="label" for="ms-target">Canister</label>
        {#if canisterOptions.length}
          <select
            id="ms-target"
            class="input text-xs font-mono"
            bind:value={targetCanister}
            onchange={onCanisterChange}
          >
            {#each canisterOptions as opt (opt.id)}
              <option value={opt.id}>{opt.label}</option>
            {/each}
          </select>
        {:else}
          <input
            id="ms-target"
            class="input text-xs font-mono"
            bind:value={targetCanister}
            placeholder="aaaaa-aa"
            onchange={onCanisterChange}
          />
        {/if}

        <p class="label">Current controllers</p>
        {#if controllersLoading}
          <p class="text-xs text-[var(--color-text-secondary)]">Loading current controllers…</p>
        {/if}
        {#if controllersError}
          <p class="text-xs text-red-700">{controllersError}</p>
        {/if}
        {#if cachedControllers.length || liveControllers.length}
          <ul class="rounded border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-2 py-1 space-y-0.5 max-h-24 overflow-y-auto">
            {#each currentControllers as ctrl (ctrl)}
              <li class="text-xs font-mono break-all">{ctrl}</li>
            {/each}
          </ul>
        {:else if actionType === 'SetCanisterControllers' && !controllersLoading}
          <p class="text-xs text-[var(--color-text-secondary)]">
            Could not determine current controllers. You can still use Set controllers.
          </p>
        {/if}
        {#if showKnownControllersInput}
          <label class="label" for="ms-known-ctls">Current controllers (one per line)</label>
          <textarea
            id="ms-known-ctls"
            class="input text-xs font-mono min-h-[72px]"
            bind:value={knownControllersText}
          ></textarea>
          <p class="text-xs text-[var(--color-text-secondary)]">
            Paste the existing controller principals so Add/Remove can merge. Or use Set controllers.
          </p>
        {/if}

        {#if actionType === 'SetCanisterControllers'}
          <label class="label" for="ms-ctls">Controllers (one per line)</label>
          <textarea id="ms-ctls" class="input text-xs font-mono min-h-[72px]" bind:value={controllersText}></textarea>
        {:else if actionType === 'AddCanisterControllers'}
          <label class="label" for="ms-add-ctls">Principals to add (one per line)</label>
          <textarea id="ms-add-ctls" class="input text-xs font-mono min-h-[72px]" bind:value={controllersText}></textarea>
        {:else}
          <label class="label" for="ms-rem-ctls">Principals to remove (one per line)</label>
          <textarea id="ms-rem-ctls" class="input text-xs font-mono min-h-[72px]" bind:value={controllersText}></textarea>
        {/if}
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
      {:else if actionType === 'DestroyCanisters'}
        <label class="label" for="ms-destroy-ids">Canister ids (one per line)</label>
        <textarea
          id="ms-destroy-ids"
          class="input text-xs font-mono min-h-[72px]"
          bind:value={destroyIdsText}
          placeholder="aaaaa-aa&#10;bbbbb-bb"
        ></textarea>
      {:else}
        <label class="label" for="ms-baton">Baton id</label>
        <input id="ms-baton" class="input text-xs font-mono" bind:value={batonId} placeholder="aaaaa-aa" />
        <label class="label" for="ms-cmd">Commander</label>
        <input id="ms-cmd" class="input text-xs font-mono" bind:value={commander} placeholder="aaaaa-aa" />
      {/if}

      {#if error}
        <p class="text-xs text-red-700">{error}</p>
      {/if}

      <button type="submit" class="btn-primary btn-sm w-full" disabled={submitDisabled}>
        {busy ? 'Submitting…' : 'Submit'}
      </button>
    </form>
  {/if}
</div>