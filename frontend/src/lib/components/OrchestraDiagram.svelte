<script lang="ts">
  import type { Tree, Canister, OrchestrationStatus } from '$lib/api';
  import { shortHash, canisterLink } from '$lib/api';
  import { colorAt } from '$lib/charts';
  import {
    sortCanistersForDisplay,
    mergeBatonStatus,
    findBatonsInTree,
    resolveBatons,
    canisterGovernanceMeta,
    governanceConsolePath,
    isBatonCanister,
    isMultisigCanister,
    isCasalsCanister,
    controllerEntries,
  } from '$lib/orchestraGovernance';
  import CanisterTypeBadges from '$lib/components/CanisterTypeBadges.svelte';

  interface Props {
    tree: Tree;
    orchestrationStatus?: OrchestrationStatus | null;
    principalLabels?: Map<string, string>;
  }

  let {
    tree,
    orchestrationStatus = null,
    principalLabels = new Map(),
  }: Props = $props();

  type HoverTarget = { section: string; stand: string; canister: Canister };

  let hovered = $state<HoverTarget | null>(null);

  const batons = $derived(
    mergeBatonStatus(findBatonsInTree(tree), resolveBatons(orchestrationStatus, tree)),
  );

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
        <span class="text-primary-400">· IC controllers listed on each canister</span>
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
          <div
            class="flex flex-col w-[min(100%,320px)] shrink-0 rounded-xl border bg-white shadow-sm overflow-hidden"
            style="border-color: {accent}33"
          >
            <div class="px-3 py-2.5 border-b" style="background: {accent}12; border-color: {accent}22">
              <div class="flex items-start gap-2">
                <span class="w-2 h-2 rounded-full mt-1.5 shrink-0" style="background: {accent}"></span>
                <div class="min-w-0">
                  <div class="text-sm font-semibold text-primary-900 truncate">{section.name}</div>
                  {#if section.description}
                    <div class="text-[11px] text-primary-500 mt-0.5 line-clamp-2">{section.description}</div>
                  {/if}
                </div>
              </div>
            </div>

            <div class="p-3 space-y-3 flex-1">
              {#if section.stands.length === 0}
                <div class="text-xs text-primary-400 italic py-2 text-center">No stands</div>
              {/if}
              {#each section.stands as stand (`${section.name}/${stand.name}`)}
                <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50/40 overflow-hidden">
                  <div class="px-2.5 py-2 border-b border-[var(--color-border-primary)] bg-white/80">
                    <div class="text-xs font-semibold text-primary-800 truncate">{stand.name}</div>
                    {#if stand.description}
                      <div class="text-[10px] text-primary-400 mt-0.5 line-clamp-2">{stand.description}</div>
                    {/if}
                  </div>

                  <div class="p-2 flex flex-col gap-2 min-h-[2.5rem]">
                    {#if stand.canisters.length === 0}
                      <span class="text-[10px] text-primary-400 italic px-1 py-1">No canisters</span>
                    {/if}
                    {#each sortCanistersForDisplay(stand.canisters) as canister, ci (canister.canister_id || `${section.name}/${stand.name}/${canister.name}/${ci}`)}
                      {@const gov = canisterGovernanceMeta(canister, batons, tree)}
                      {@const ctrls = controllerEntries(canister.controllers, principalLabels)}
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

                        <span class="flex items-center gap-1 flex-wrap text-[9px] uppercase tracking-wide font-semibold text-primary-600">
                          <span class="{isCasalsCanister(canister) ? 'text-primary-700' : canister.kind === 'frontend' ? 'text-blue-700' : isMultisigCanister(canister) ? 'text-emerald-700' : isBatonCanister(canister) ? 'text-orange-700' : 'text-violet-700'}">
                            {canister.kind}
                          </span>
                          {#if canister.status}
                            <span class="text-primary-500 normal-case">· {canister.status}</span>
                          {/if}
                        </span>

                        {#if gov.managedBy && !gov.isBaton}
                          <span class="text-[10px] text-orange-700">
                            managed · {gov.managedBy.name}
                            {#if gov.batonIsController}
                              <span class="text-emerald-700"> · IC ctrl</span>
                            {/if}
                          </span>
                        {/if}

                        <div class="pt-1 border-t border-black/5">
                          <p class="text-[9px] font-semibold uppercase tracking-wider text-primary-500 mb-0.5">Controllers</p>
                          {#if ctrls.length === 0}
                            <p class="text-[10px] text-primary-400 italic">none cached</p>
                          {:else}
                            <ul class="space-y-0.5">
                              {#each ctrls as ctrl (ctrl.principal)}
                                <li class="text-[10px] font-mono text-primary-700 truncate" title={ctrl.title}>
                                  {ctrl.display}
                                </li>
                              {/each}
                            </ul>
                          {/if}
                        </div>
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
          {@const hovCtrls = controllerEntries(hovered.canister.controllers, principalLabels)}
          <div class="space-y-1">
            <div class="truncate">
              {hovered.section} / {hovered.stand} / {hovered.canister.name}
              {#if hovered.canister.canister_id}
                · {hovered.canister.canister_id}
              {/if}
              {#if hovered.canister.wasm_hash}
                · {shortHash(hovered.canister.wasm_hash)}
              {/if}
            </div>
            {#if hovCtrls.length}
              <div class="font-sans text-primary-500">
                controllers:
                {#each hovCtrls as ctrl, i (ctrl.principal)}
                  {#if i > 0}, {/if}<span title={ctrl.title}>{ctrl.display}</span>
                {/each}
              </div>
            {/if}
          </div>
        {:else}
          <span class="text-primary-400 font-sans">Hover a canister for path, ID, and controller details.</span>
        {/if}
      </div>
    </div>
  {/if}
</div>
