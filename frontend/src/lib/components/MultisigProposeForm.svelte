<script lang="ts">
  import type { AuthorizedWasm, Sheet, Tree } from '$lib/api';
  import { backendCanisterId, casalsMetadata, getSheetDocument, getTree, listAuthorizedWasms, storeBundle } from '$lib/api';
  import { defaultNamespaceFor, knownContentNamespaces, namespaceOk } from '$lib/contentDeploy';
  import { isFrontend } from '$lib/orchestraList';
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
  import { upgradeMemoryKeepForWasm } from '$lib/batonUpgrade';
  import { catalogForTarget, defaultInstallArg, storeKey } from '$lib/wasmStorePath';

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

  // Upgrade canister: catalog + store come from Casals; the committee only
  // streams what an operator already authorized.
  let catalog = $state<AuthorizedWasm[]>([]);
  let catalogLoading = $state(false);
  let storeId = $state('');
  let wasmKey = $state('');

  // Deploy frontend bundle: the committee asks the conductor (`deploy_content`)
  // to ship a store namespace to a frontend; the store's bundle hash is pinned.
  let storedSheet = $state<Sheet | null>(null);
  let bundleNamespace = $state('');
  let bundleHash = $state('');
  let bundleFiles = $state(0);
  let bundleReading = $state(false);
  let bundleError = $state('');
  const isBundleAction = $derived(actionType === 'DeployBundle');
  const bundleNamespaces = $derived(knownContentNamespaces(storedSheet));
  const bundleTargets = $derived(canisterOptions.filter((o) => isFrontend({ kind: 'backend', wasm_key: o.wasm_key, wasm_type: o.wasm_type })));

  async function ensureStoredSheet() {
    if (storedSheet) return;
    storedSheet = await getSheetDocument().then((d) => d.sheet).catch(() => null);
  }

  async function readBundle() {
    bundleHash = '';
    bundleFiles = 0;
    bundleError = '';
    const ns = bundleNamespace.trim();
    if (!ns || !namespaceOk(ns)) return;
    bundleReading = true;
    try {
      const b = await storeBundle(ns);
      bundleFiles = Object.keys(b.files ?? {}).length;
      if (!bundleFiles) bundleError = 'The store holds nothing under this namespace — upload the bundle first (Files → Upload bundle).';
      else bundleHash = b.bundle_sha256;
    } catch (e: unknown) {
      bundleError = e instanceof Error ? e.message : String(e);
    } finally {
      bundleReading = false;
    }
  }

  async function syncBundleTarget() {
    if (!isBundleAction) return;
    await ensureStoredSheet();
    if (!bundleTargets.some((o) => o.id === targetCanister)) targetCanister = bundleTargets[0]?.id ?? '';
    const name = canisterOptions.find((o) => o.id === targetCanister)?.label ?? '';
    bundleNamespace = defaultNamespaceFor(storedSheet, name);
    await readBundle();
  }

  const isControllerAction = $derived(
    actionType === 'SetCanisterControllers' ||
      actionType === 'AddCanisterControllers' ||
      actionType === 'RemoveCanisterControllers',
  );
  const isUpgradeAction = $derived(actionType === 'UpgradeCanister');
  /** Actions that call the IC management canister: the committee must be a controller. */
  const needsControl = $derived(isControllerAction || isUpgradeAction);

  interface CanisterOption {
    id: string;
    label: string;
    controllers?: string[];
    /** true / false from the tree's cached controllers; undefined when unknown. */
    controlled?: boolean;
    wasm_key?: string;
    wasm_type?: string;
    wasm_hash?: string;
  }

  function isSelf(p: string): boolean {
    return p.toLowerCase() === canisterId.toLowerCase();
  }

  const canisterOptions = $derived.by((): CanisterOption[] => {
    const src = loadedTree ?? tree;
    if (!src) return [];
    const out: CanisterOption[] = [];
    for (const sec of src.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (!c.canister_id) continue;
          const ctrls = c.controllers;
          out.push({
            id: c.canister_id,
            label: c.name,
            controllers: ctrls,
            controlled: ctrls && ctrls.length ? ctrls.some(isSelf) : undefined,
            wasm_key: c.wasm_key,
            wasm_type: c.wasm_type,
            wasm_hash: c.wasm_hash,
          });
        }
      }
    }
    return out;
  });

  /** Targets the committee can act on via the management canister (unknown = allowed, checked at submit). */
  const controlledOptions = $derived(canisterOptions.filter((o) => o.controlled !== false));
  const uncontrolledOptions = $derived(canisterOptions.filter((o) => o.controlled === false));

  const selectedOption = $derived(canisterOptions.find((o) => o.id === targetCanister));
  const upgradeTargets = $derived(controlledOptions.filter((o) => !isSelf(o.id)));
  // Strict: same family as the target, else same type — never the whole catalog
  // for a known target. A wrong module is rolled back by the IC when its
  // post_upgrade traps, but it should not be one click away.
  const wasmOptions = $derived(
    selectedOption ? catalogForTarget(selectedOption, catalog) : catalog,
  );
  const selectedWasm = $derived(wasmOptions.find((w) => w.key === wasmKey));

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
          actionType === 'RemoveCanisterControllers')) ||
      (isUpgradeAction && (catalogLoading || !selectedWasm || !storeId || !targetCanister)),
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

  async function ensureCatalog() {
    if (catalog.length && storeId) return;
    catalogLoading = true;
    try {
      const [wasms, meta] = await Promise.all([
        listAuthorizedWasms().catch(() => [] as AuthorizedWasm[]),
        casalsMetadata().catch(() => null),
      ]);
      catalog = wasms;
      storeId = (meta?.wasm_store_canister_id || '').trim();
    } finally {
      catalogLoading = false;
    }
  }

  function syncWasmKey() {
    if (!isUpgradeAction) return;
    const current = (selectedOption?.wasm_key || '').trim();
    wasmKey = wasmOptions.some((w) => w.key === current) ? current : wasmOptions[0]?.key ?? '';
  }

  /** Controller and upgrade actions default to a canister the committee controls. */
  function pickDefaultTarget() {
    const pool = isUpgradeAction ? upgradeTargets : needsControl ? controlledOptions : canisterOptions;
    if (!pool.some((o) => o.id === targetCanister)) {
      targetCanister = pool[0]?.id ?? '';
    }
  }

  async function onActionTypeChange() {
    pickDefaultTarget();
    syncControllersTextForAction();
    if (isBundleAction) {
      clearControllerFetchState();
      await syncBundleTarget();
      return;
    }
    if (isUpgradeAction) {
      clearControllerFetchState();
      await ensureCatalog();
      syncWasmKey();
      return;
    }
    if (isControllerAction) {
      await loadLiveControllers();
    } else {
      clearControllerFetchState();
    }
  }

  async function onCanisterChange() {
    syncControllersTextForAction();
    if (isBundleAction) {
      await syncBundleTarget();
      return;
    }
    if (isUpgradeAction) {
      syncWasmKey();
      return;
    }
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

    const call = isUpgradeAction ? '`install_chunked_code`' : '`update_settings`';
    let message =
      `This committee is not an IC controller of the target, so ${call} will fail. ` +
      `Current controllers: ${controllerList}.`;

    if (standBaton?.canister_id) {
      const batonLabel = standBaton.name
        ? `${standBaton.canister_id} (${standBaton.name})`
        : standBaton.canister_id;
      message += isUpgradeAction
        ? ` This stand is handed to baton ${batonLabel}: upgrade it from the stand page (Baton managed upgrade),` +
          ' or bump the version in the sheet and let `casals up` file the Baton proposal for the committee to approve.'
        : ` After baton hand-off, this stand is controlled by baton ${batonLabel}.` +
          ' Add this committee via an existing controller first, or use that controller directly.';
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
    targetCanister = '';
    pickDefaultTarget();
    wasmKey = '';
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
      if (needsControl) {
        controllers = await resolveControllersForSubmit(id);
        if (!multisigControlsTarget(controllers)) {
          throw multisigNotControllerError(controllers);
        }
      }
      if (isUpgradeAction && !selectedWasm) {
        throw new Error('Pick a WASM from the catalog');
      }

      const action = buildMultisigAction(actionType, {
        store: storeId,
        store_key: selectedWasm
          ? storeKey(selectedWasm.registry_namespace, selectedWasm.registry_path)
          : '',
        sha256: selectedWasm?.wasm_hash ?? '',
        arg: selectedWasm ? defaultInstallArg(selectedWasm, selectedOption ?? {}) : undefined,
        wasm_memory_keep: selectedWasm ? upgradeMemoryKeepForWasm(selectedWasm) : false,
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
        target_name: selectedOption?.label ?? '',
        namespace: bundleNamespace,
        bundle_sha256: bundleHash,
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
        <option value="UpgradeCanister">Upgrade canister</option>
        <option value="DeployBundle">Deploy frontend bundle</option>
        <option value="ManageSigners">Manage signers</option>
        <option value="AddCommander">Add baton commander</option>
        <option value="RemoveCommander">Remove baton commander</option>
        <option value="DestroyStand">Destroy stand</option>
        <option value="DestroyCanister">Destroy canister</option>
        <option value="DestroyCanisters">Destroy canisters (batch)</option>
      </select>

      {#if needsControl}
        {@const pool = isUpgradeAction ? upgradeTargets : controlledOptions}
        <label class="label" for="ms-target">Canister</label>
        {#if canisterOptions.length}
          <select
            id="ms-target"
            class="input text-xs font-mono"
            bind:value={targetCanister}
            onchange={onCanisterChange}
          >
            {#each pool as opt (opt.id)}
              <option value={opt.id}>
                {opt.label}{opt.controlled === undefined ? ' (controllers unknown)' : ''}
              </option>
            {/each}
            {#if uncontrolledOptions.length}
              <optgroup label="Not controlled by this committee">
                {#each uncontrolledOptions as opt (opt.id)}
                  <option value={opt.id} disabled>{opt.label}</option>
                {/each}
              </optgroup>
            {/if}
          </select>
          {#if !pool.length}
            <p class="text-xs text-[var(--color-text-secondary)]">
              This committee is not an IC controller of any canister in the orchestra. Stands handed to
              a Baton are upgraded through that Baton; add the committee as a controller first for the rest.
            </p>
          {:else if uncontrolledOptions.length}
            <p class="text-xs text-[var(--color-text-secondary)]">
              Greyed-out canisters have other controllers (usually a stand Baton), so the committee cannot
              act on them directly.
            </p>
          {/if}
        {:else}
          <input
            id="ms-target"
            class="input text-xs font-mono"
            bind:value={targetCanister}
            placeholder="aaaaa-aa"
            onchange={onCanisterChange}
          />
        {/if}
      {/if}

      {#if isBundleAction}
        <label class="label" for="ms-bundle-target">Frontend</label>
        {#if bundleTargets.length}
          <select id="ms-bundle-target" class="input text-xs font-mono" bind:value={targetCanister} onchange={onCanisterChange}>
            {#each bundleTargets as opt (opt.id)}
              <option value={opt.id}>{opt.label}</option>
            {/each}
          </select>
        {:else}
          <p class="text-xs text-red-700">No frontend (asset canister) in the orchestra.</p>
        {/if}
        <label class="label" for="ms-bundle-ns">Store namespace</label>
        <input id="ms-bundle-ns" class="input text-xs font-mono" list="ms-bundle-namespaces" bind:value={bundleNamespace} onchange={readBundle} placeholder="frontend/<app>-assets/main" />
        <datalist id="ms-bundle-namespaces">
          {#each bundleNamespaces as ns (ns)}<option value={ns}></option>{/each}
        </datalist>
        {#if bundleReading}
          <p class="text-xs text-[var(--color-text-secondary)]">Reading the store…</p>
        {:else if bundleError}
          <p class="text-xs text-amber-700">{bundleError}</p>
        {:else if bundleHash}
          <p class="text-xs text-[var(--color-text-secondary)] font-mono break-all">
            Store bundle {bundleFiles} file(s) · {bundleHash}
          </p>
          <p class="text-xs text-[var(--color-text-secondary)]">
            On approval the committee calls the conductor's <span class="font-mono">deploy_content</span>; the conductor writes this
            bundle into the frontend in rounds, refusing if the store changed meanwhile.
          </p>
        {/if}
      {/if}

      {#if isUpgradeAction}
        <label class="label" for="ms-wasm">WASM (authorized catalog)</label>
        {#if catalogLoading}
          <p class="text-xs text-[var(--color-text-secondary)]">Loading catalog…</p>
        {:else if wasmOptions.length}
          <select id="ms-wasm" class="input text-xs font-mono" bind:value={wasmKey}>
            {#each wasmOptions as w (w.key)}
              <option value={w.key}>{w.key} · {w.wasm_hash.slice(0, 8)}</option>
            {/each}
          </select>
        {:else}
          <p class="text-xs text-red-700">
            No authorized WASM matches this canister's family or type. Upload and authorize one on the
            WASMs page first.
          </p>
        {/if}
        {#if selectedOption}
          <p class="text-xs text-[var(--color-text-secondary)] font-mono break-all">
            Now: {selectedOption.wasm_key || '—'}
            {selectedOption.wasm_hash ? ` · ${selectedOption.wasm_hash.slice(0, 8)}` : ''}
          </p>
        {/if}
        {#if selectedWasm}
          <p class="text-xs text-[var(--color-text-secondary)]">
            The committee streams {storeKey(selectedWasm.registry_namespace, selectedWasm.registry_path)}
            from the WASM store into the target and installs it in upgrade mode, pinned to
            sha256 {selectedWasm.wasm_hash.slice(0, 12)}…
            {upgradeMemoryKeepForWasm(selectedWasm) ? ' (Motoko: main memory kept).' : '.'}
            {#if !storeId}
              <span class="text-red-700">WASM store id unknown — run `casals up` first.</span>
            {/if}
          </p>
        {/if}
      {/if}

      {#if isControllerAction}
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
      {:else if isUpgradeAction}
        <!-- fields rendered above -->
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