<script lang="ts">
  import type { Tree, Canister } from '$lib/api';
  import { shortHash, canisterLink } from '$lib/api';
  import { colorAt } from '$lib/charts';

  interface Props {
    tree: Tree;
  }

  let { tree }: Props = $props();

  type HoverTarget = { section: string; stand: string; canister: Canister };

  let hovered = $state<HoverTarget | null>(null);

  const totalCanisters = $derived(
    tree.sections.reduce((n, sec) => n + sec.stands.reduce((m, d) => m + d.canisters.length, 0), 0),
  );
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
        <span class="flex items-center gap-1.5" title="Top-level grouping">
          <span class="w-3 h-3 rounded border border-primary-300 bg-white shrink-0"></span>
          Section
        </span>
        <span class="flex items-center gap-1.5" title="Application instance inside a section">
          <span class="w-3 h-3 rounded border border-[var(--color-border-primary)] bg-primary-50/80 shrink-0"></span>
          Stand
        </span>
        <span class="flex items-center gap-1.5" title="Deployed canister (click to open)">
          <span class="w-3 h-2 rounded border border-violet-200 bg-violet-50 shrink-0"></span>
          backend canister
        </span>
        <span class="flex items-center gap-1.5">
          <span class="w-3 h-2 rounded border border-blue-200 bg-blue-50 shrink-0"></span>
          frontend canister
        </span>
        <span class="text-primary-400">· click a canister to open</span>
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
      </div>
    </div>

    <!-- Sections -->
    <div class="overflow-x-auto pb-2 -mx-1 px-1">
      <div class="flex gap-4 min-w-min mx-auto justify-center">
        {#each tree.sections as section, si (section.name)}
          {@const accent = colorAt(si)}
          <div
            class="flex flex-col w-[min(100%,280px)] shrink-0 rounded-xl border bg-white shadow-sm overflow-hidden"
            style="border-color: {accent}33"
          >
            <!-- Section header -->
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

            <!-- Stands -->
            <div class="p-3 space-y-3 flex-1">
              {#if section.stands.length === 0}
                <div class="text-xs text-primary-400 italic py-2 text-center">No stands</div>
              {/if}
              {#each section.stands as stand (stand.name)}
                <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50/40 overflow-hidden">
                  <div class="px-2.5 py-2 border-b border-[var(--color-border-primary)] bg-white/80">
                    <div class="text-xs font-semibold text-primary-800 truncate">{stand.name}</div>
                    {#if stand.description}
                      <div class="text-[10px] text-primary-400 mt-0.5 line-clamp-2">{stand.description}</div>
                    {/if}
                  </div>

                  <div class="p-2 flex flex-wrap gap-1.5 min-h-[2.5rem] items-start content-start">
                    {#if stand.canisters.length === 0}
                      <span class="text-[10px] text-primary-400 italic px-1 py-1">No canisters</span>
                    {/if}
                    {#each stand.canisters as canister (canister.name)}
                      <a
                        href={canisterLink(canister)}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="group inline-flex flex-col gap-0.5 max-w-full px-2 py-1.5 rounded-md border text-left transition-all duration-150
                               {canister.kind === 'frontend'
                          ? 'bg-blue-50/80 border-blue-200 hover:border-blue-300 hover:shadow-sm'
                          : 'bg-violet-50/80 border-violet-200 hover:border-violet-300 hover:shadow-sm'}
                               {hovered?.canister.name === canister.name && hovered?.stand === stand.name && hovered?.section === section.name
                          ? 'ring-2 ring-primary-400 ring-offset-1'
                          : ''}"
                        title="{canister.canister_id || canister.name}"
                        onmouseenter={() => (hovered = { section: section.name, stand: stand.name, canister })}
                        onmouseleave={() => (hovered = null)}
                      >
                        <span class="flex items-center gap-1 min-w-0">
                          <span
                            class="w-1.5 h-1.5 rounded-full shrink-0 {canister.kind === 'frontend' ? 'bg-blue-500' : 'bg-violet-500'}"
                          ></span>
                          <span class="text-[11px] font-medium text-primary-900 truncate">{canister.name}</span>
                        </span>
                        <span class="flex items-center gap-1 flex-wrap">
                          <span class="text-[9px] uppercase tracking-wide font-semibold {canister.kind === 'frontend' ? 'text-blue-700' : 'text-violet-700'}">
                            {canister.kind}
                          </span>
                          {#if canister.status}
                            <span class="text-[9px] text-primary-500">· {canister.status}</span>
                          {/if}
                        </span>
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

    <!-- Hover detail -->
    <div class="mt-4 pt-3 border-t border-[var(--color-border-primary)] flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-primary-500">
      <div class="min-h-[1.25rem] font-mono text-[11px] text-primary-600 truncate">
        {#if hovered}
          {hovered.section} / {hovered.stand} / {hovered.canister.name}
          {#if hovered.canister.canister_id}
            · {hovered.canister.canister_id}
          {/if}
          {#if hovered.canister.wasm_hash}
            · {shortHash(hovered.canister.wasm_hash)}
          {/if}
        {:else}
          <span class="text-primary-400 font-sans">Hover a canister for path and canister details.</span>
        {/if}
      </div>
    </div>
  {/if}
</div>
