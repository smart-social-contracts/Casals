<script lang="ts">
  import type { Tree, Canister, Section, Stand } from '$lib/api';
  import { shortHash, shortPrincipal, canisterLink } from '$lib/api';
  import { colorAt } from '$lib/charts';
  import {
    sortCanistersForDisplay,
    findBatonsInTree,
    canisterGovernanceMeta,
    governanceConsolePath,
    isBatonCanister,
    isMultisigCanister,
    isCasalsCanister,
  } from '$lib/orchestraGovernance';
  import { entityCommanders, isUnclaimedSlot } from '$lib/commanderAccess';
  import { isOrchestraSectionName } from '$lib/governanceUx';
  import CanisterTypeBadges from '$lib/components/CanisterTypeBadges.svelte';
  import SubnetFlags from '$lib/components/SubnetFlags.svelte';

  interface Props {
    tree: Tree;
    /** Shown in place of the synthetic Casals section's name. */
    orchestraName?: string;
    /** Render the section / stand management buttons (the signed-in operator). */
    canManage?: boolean;
    onCreateStand?: (section: Section) => void;
    onAddSectionCommander?: (section: Section) => void;
    onRenameSection?: (section: Section) => void;
    onDeleteSection?: (section: Section) => void;
    onCreateCanister?: (stand: Stand) => void;
    onRegisterCanister?: (stand: Stand) => void;
    onUpgradeStand?: (stand: Stand) => void;
    onAddStandCommander?: (stand: Stand) => void;
    onRenameStand?: (stand: Stand) => void;
    onDeleteStand?: (stand: Stand) => void;
  }

  let {
    tree,
    orchestraName = '',
    canManage = false,
    onCreateStand,
    onAddSectionCommander,
    onRenameSection,
    onDeleteSection,
    onCreateCanister,
    onRegisterCanister,
    onUpgradeStand,
    onAddStandCommander,
    onRenameStand,
    onDeleteStand,
  }: Props = $props();

  function shortId(id: string): string {
    return id.length > 13 ? `${id.slice(0, 5)}…${id.slice(-5)}` : id;
  }
  function placementLabel(e: { subnet?: string; subnet_type?: string }): string {
    if (e.subnet) return `subnet ${shortId(e.subnet)}`;
    if (e.subnet_type) return `subnet type: ${e.subnet_type}`;
    return '';
  }

  type HoverTarget = { section: string; stand: string; canister: Canister };

  let hovered = $state<HoverTarget | null>(null);

  const batons = $derived(findBatonsInTree(tree));

  const totalCanisters = $derived(
    tree.sections.reduce((n, sec) => n + sec.stands.reduce((m, d) => m + d.canisters.length, 0), 0),
  );

  function chipClasses(canister: Canister): string {
    if (isCasalsCanister(canister)) {
      return 'bg-primary-50/90 border-primary-300 hover:border-primary-400 ring-primary-100';
    }
    if (isMultisigCanister(canister)) {
      return 'bg-emerald-50/90 border-emerald-300 hover:border-emerald-400 ring-emerald-100';
    }
    if (isBatonCanister(canister)) {
      return 'bg-orange-50/90 border-orange-300 hover:border-orange-400 ring-orange-100';
    }
    if (canister.kind === 'frontend') {
      return 'bg-blue-50/80 border-blue-200 hover:border-blue-300';
    }
    return 'bg-violet-50/80 border-violet-200 hover:border-violet-300';
  }

  function chipLink(canister: Canister): string {
    return governanceConsolePath(canister) ?? canisterLink(canister);
  }

  function chipTarget(canister: Canister): string | undefined {
    return governanceConsolePath(canister) ? undefined : '_blank';
  }
</script>

<div class="w-full">
  {#if tree.sections.length === 0}
    <div class="flex items-center justify-center text-sm text-primary-400 py-16">
      Nothing to diagram yet.
    </div>
  {:else}
    <!-- Legend -->
    <div class="mb-4 rounded-lg border border-[var(--color-border-primary)] bg-primary-50/60 px-3 py-2.5">
      <div class="text-[10px] font-semibold uppercase tracking-wider text-primary-500 mb-2">Legend</div>
      <div class="flex flex-wrap gap-x-5 gap-y-2 text-xs text-primary-600">
        <span class="flex items-center gap-1.5">
          <span class="w-3 h-2 rounded border border-primary-300 bg-primary-50 shrink-0"></span>
          casals
        </span>
        <span class="flex items-center gap-1.5">
          <span class="w-3 h-2 rounded border border-emerald-300 bg-emerald-50 shrink-0"></span>
          multisig
        </span>
        <span class="flex items-center gap-1.5">
          <span class="w-3 h-2 rounded border border-orange-300 bg-orange-50 shrink-0"></span>
          baton
        </span>
        <span class="flex items-center gap-1.5">
          <span class="w-3 h-2 rounded border border-violet-200 bg-violet-50 shrink-0"></span>
          backend
        </span>
        <span class="flex items-center gap-1.5">
          <span class="w-3 h-2 rounded border border-blue-200 bg-blue-50 shrink-0"></span>
          frontend
        </span>
      </div>
    </div>

    <!-- Root -->
    <div class="flex flex-col items-center mb-6">
      <div
        class="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-primary-200 bg-primary-50 text-sm font-semibold text-primary-800 shadow-sm"
      >
        <svg class="w-4 h-4 text-primary-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 9V18m0 0a3 3 0 11-6 0 3 3 0 016 0zm12-3v6m0 0a3 3 0 11-6 0 3 3 0 016 0zM9 9l12-3" />
        </svg>
        Orchestra
      </div>
      <div class="w-px h-5 bg-primary-200" aria-hidden="true"></div>
      <div class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">
        {tree.sections.length} section{tree.sections.length === 1 ? '' : 's'} · {totalCanisters} canister{totalCanisters === 1 ? '' : 's'}
        {#if batons.length}
          · {batons.length} baton{batons.length === 1 ? '' : 's'}
        {/if}
      </div>
    </div>

    <!-- Sections -->
    <div class="overflow-x-auto pb-2 -mx-1 px-1">
      <div class="flex gap-4 min-w-min mx-auto justify-center">
        {#each tree.sections as section, si (`${section.name}|${si}`)}
          {@const accent = colorAt(si)}
          {@const core = isOrchestraSectionName(section.name)}
          <div
            class="flex flex-col w-[min(100%,320px)] shrink-0 rounded-xl border bg-white shadow-sm overflow-hidden"
            style="border-color: {accent}33"
          >
            <div class="px-3 py-2.5 border-b" style="background: {accent}12; border-color: {accent}22">
              <div class="flex items-start justify-between gap-2">
                <div class="flex items-start gap-2 min-w-0">
                  <span class="w-2 h-2 rounded-full mt-1.5 shrink-0" style="background: {accent}"></span>
                  <div class="min-w-0">
                    <div class="flex items-center gap-1.5 min-w-0">
                      {#if core}<span class="badge shrink-0 bg-primary-800 text-white border border-primary-800">orchestra</span>{/if}
                      <span class="text-sm font-semibold text-primary-900 truncate">{core ? (orchestraName || section.name) : section.name}</span>
                    </div>
                    {#if core}
                      <div class="text-[11px] text-primary-500 mt-0.5">Casals system canisters. Commanders here act on every section and stand.</div>
                    {:else if section.description}
                      <div class="text-[11px] text-primary-500 mt-0.5 line-clamp-2">{section.description}</div>
                    {/if}
                    {#each entityCommanders(section) as cmd (cmd.principal)}
                      <div class="text-[10px] text-primary-400 mt-0.5 font-mono truncate" title={cmd.principal}>
                        {core ? 'orchestra commander' : 'commander'}:
                        {#if isUnclaimedSlot(cmd)}<span class="italic">pending access code</span>{:else}{shortPrincipal(cmd.principal)}{/if}
                      </div>
                    {/each}
                    {#if placementLabel(section)}
                      <div class="flex items-center gap-1.5 text-[10px] text-primary-400 mt-0.5 font-mono" title={section.subnet || section.subnet_type}>
                        <span>⬡ {placementLabel(section)}</span>
                        {#if section.subnet}<SubnetFlags subnetId={section.subnet} hoverOnly />{/if}
                      </div>
                    {/if}
                  </div>
                </div>
                {#if canManage}
                  <div class="flex items-center gap-0.5 shrink-0 -mr-1">
                    <button class="icon-btn" aria-label="Add stand" onclick={() => onCreateStand?.(section)}>
                      <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
                    </button>
                    <button class="icon-btn" aria-label="Add commander" onclick={() => onAddSectionCommander?.(section)}>
                      <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0"/></svg>
                    </button>
                    {#if !core}
                      <button class="icon-btn" aria-label="Rename section" onclick={() => onRenameSection?.(section)}>
                        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                      </button>
                      <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" aria-label="Delete section" onclick={() => onDeleteSection?.(section)}>
                        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                      </button>
                    {/if}
                  </div>
                {/if}
              </div>
            </div>

            <div class="p-3 space-y-3 flex-1">
              {#if section.stands.length === 0}
                <div class="text-xs text-primary-400 italic py-2 text-center">No stands</div>
              {/if}
              {#each section.stands as stand (`${section.name}/${stand.name}`)}
                <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50/40 overflow-hidden">
                  <div class="px-2.5 py-2 border-b border-[var(--color-border-primary)] bg-white/80 flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="text-xs font-semibold text-primary-800 truncate">{stand.name}</div>
                      {#if stand.description}
                        <div class="text-[10px] text-primary-400 mt-0.5 line-clamp-2">{stand.description}</div>
                      {/if}
                      {#each entityCommanders(stand) as cmd (cmd.principal)}
                        <div class="text-[10px] text-primary-400 mt-0.5 font-mono truncate" title={cmd.principal}>
                          commander:
                          {#if isUnclaimedSlot(cmd)}<span class="italic">pending access code</span>{:else}{shortPrincipal(cmd.principal)}{/if}
                        </div>
                      {/each}
                      {#if placementLabel(stand)}
                        <div class="flex items-center gap-1.5 text-[10px] text-primary-400 mt-0.5 font-mono" title={stand.subnet || stand.subnet_type}>
                          <span>⬡ {placementLabel(stand)}</span>
                          {#if stand.subnet}<SubnetFlags subnetId={stand.subnet} hoverOnly />{/if}
                        </div>
                      {/if}
                    </div>
                    {#if canManage}
                      <div class="flex items-center gap-0.5 shrink-0 -mr-1">
                        <!-- Casals core stands are declared by the sheet's conductor/governance blocks: no add/register/deploy/rename/delete. -->
                        {#if !core}
                          <button class="icon-btn" aria-label="Add canister" onclick={() => onCreateCanister?.(stand)}>
                            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
                          </button>
                          <button class="icon-btn" aria-label="Register existing canister" onclick={() => onRegisterCanister?.(stand)}>
                            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244"/></svg>
                          </button>
                          <button class="icon-btn" aria-label="Deploy all canisters in stand" onclick={() => onUpgradeStand?.(stand)}>
                            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"/></svg>
                          </button>
                        {/if}
                        <button class="icon-btn" aria-label="Add commander" onclick={() => onAddStandCommander?.(stand)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0"/></svg>
                        </button>
                        {#if !core}
                          <button class="icon-btn" aria-label="Rename stand" onclick={() => onRenameStand?.(stand)}>
                            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                          </button>
                          <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" aria-label="Delete stand" onclick={() => onDeleteStand?.(stand)}>
                            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                          </button>
                        {/if}
                      </div>
                    {/if}
                  </div>

                  <div class="p-2 flex flex-col gap-2 min-h-[2.5rem]">
                    {#if stand.canisters.length === 0}
                      <span class="text-[10px] text-primary-400 italic px-1 py-1">No canisters</span>
                    {/if}
                    {#each sortCanistersForDisplay(stand.canisters) as canister, ci (canister.canister_id || `${section.name}/${stand.name}/${canister.name}/${ci}`)}
                      {@const gov = canisterGovernanceMeta(canister, batons, tree)}
                      <a
                        href={chipLink(canister)}
                        target={chipTarget(canister)}
                        rel={chipTarget(canister) ? 'noopener noreferrer' : undefined}
                        class="group flex flex-col gap-1 max-w-full px-2 py-2 rounded-md border text-left transition-all duration-150 hover:shadow-sm
                               {chipClasses(canister)}
                               {hovered?.canister.name === canister.name && hovered?.stand === stand.name && hovered?.section === section.name
                          ? 'ring-2 ring-primary-400 ring-offset-1'
                          : ''}"
                        title="{canister.canister_id || canister.name}"
                        onmouseenter={() => (hovered = { section: section.name, stand: stand.name, canister })}
                        onmouseleave={() => (hovered = null)}
                      >
                        <span class="flex items-center gap-1 min-w-0 flex-wrap">
                          <span class="text-[11px] font-semibold text-primary-900 truncate">{canister.name}</span>
                          <CanisterTypeBadges {canister} />
                        </span>

                        <span class="text-[9px] uppercase tracking-wide font-semibold {isCasalsCanister(canister) ? 'text-primary-700' : canister.kind === 'frontend' ? 'text-blue-700' : isMultisigCanister(canister) ? 'text-emerald-700' : isBatonCanister(canister) ? 'text-orange-700' : 'text-violet-700'}">
                          {canister.kind}
                        </span>

                        {#if gov.managedBy && !gov.isBaton}
                          <span class="text-[10px] text-orange-700">
                            managed · {gov.managedBy.name}
                            {#if gov.batonIsController}
                              <span class="text-emerald-700"> · IC ctrl</span>
                            {/if}
                          </span>
                        {/if}
                      </a>
                    {/each}
                  </div>
                </div>
              {/each}
            </div>
          </div>

          {#if si < tree.sections.length - 1}
            <div class="hidden lg:flex items-center self-center text-primary-300 shrink-0" aria-hidden="true">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
              </svg>
            </div>
          {/if}
        {/each}
      </div>
    </div>

    <div class="mt-4 pt-3 border-t border-[var(--color-border-primary)] flex flex-col sm:flex-row sm:items-start justify-between gap-2 text-xs text-primary-500">
      <div class="min-h-[1.25rem] font-mono text-[11px] text-primary-600">
        {#if hovered}
          <div class="truncate">
            {hovered.section} / {hovered.stand} / {hovered.canister.name}
            {#if hovered.canister.canister_id}
              · {hovered.canister.canister_id}
            {/if}
            {#if hovered.canister.wasm_hash}
              · {shortHash(hovered.canister.wasm_hash)}
            {/if}
          </div>
        {:else}
          <span class="text-primary-400 font-sans">Hover a canister for path, ID, and hash.</span>
        {/if}
      </div>
    </div>
  {/if}
</div>
