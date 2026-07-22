<script lang="ts">
  export type CanisterScopeOption = {
    key: string;
    name: string;
    canister_id: string;
    section?: string;
    stand?: string;
  };

  interface Props {
    options?: CanisterScopeOption[];
    selected?: Set<string>;
    seriesColors?: Map<string, string>;
  }

  let {
    options = [],
    selected = $bindable(new Set<string>()),
    seriesColors = new Map<string, string>(),
  }: Props = $props();

  let query = $state('');
  let listOpen = $state(false);
  let highlightIndex = $state(0);
  let rootEl: HTMLDivElement | undefined = $state();

  const selectedOptions = $derived(
    options.filter((o) => selected.has(o.key)).sort((a, b) => a.name.localeCompare(b.name)),
  );

  const suggestions = $derived.by(() => {
    const q = query.trim().toLowerCase();
    const pool = options.filter((o) => !selected.has(o.key));
    if (!q) return pool.slice(0, 20);
    return pool
      .filter(
        (o) =>
          o.name.toLowerCase().includes(q)
          || o.canister_id.toLowerCase().includes(q)
          || (o.section || '').toLowerCase().includes(q)
          || (o.stand || '').toLowerCase().includes(q),
      )
      .slice(0, 20);
  });

  function add(key: string) {
    selected = new Set([...selected, key]);
    query = '';
    listOpen = false;
    highlightIndex = 0;
  }

  function remove(key: string) {
    const next = new Set(selected);
    next.delete(key);
    selected = next;
  }

  function openList() {
    listOpen = true;
    highlightIndex = 0;
  }

  function handleInputKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      listOpen = false;
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!listOpen) openList();
      else highlightIndex = Math.min(highlightIndex + 1, Math.max(0, suggestions.length - 1));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      highlightIndex = Math.max(highlightIndex - 1, 0);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const pick = suggestions[highlightIndex];
      if (pick) add(pick.key);
    }
  }

  function handleDocumentClick(e: MouseEvent) {
    if (!rootEl?.contains(e.target as Node)) listOpen = false;
  }

  $effect(() => {
    if (typeof document === 'undefined') return;
    document.addEventListener('click', handleDocumentClick);
    return () => document.removeEventListener('click', handleDocumentClick);
  });

  $effect(() => {
    query;
    highlightIndex = 0;
  });
</script>

<div bind:this={rootEl} class="flex flex-col gap-2 mb-3">
  <div class="flex flex-wrap items-center gap-2">
    <span class="text-xs text-primary-500 shrink-0">Show</span>
    <div class="relative w-full max-w-md">
      <input
        type="search"
        class="input text-sm w-full pl-9"
        placeholder="Search canisters by name, section, stand, or ID…"
        bind:value={query}
        aria-label="Add canister to chart"
        aria-expanded={listOpen}
        aria-controls="canister-scope-suggestions"
        aria-autocomplete="list"
        role="combobox"
        autocomplete="off"
        onfocus={openList}
        oninput={openList}
        onkeydown={handleInputKeydown}
      />
      <svg
        class="w-4 h-4 text-primary-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="8" /><path stroke-linecap="round" d="M21 21l-4.35-4.35" />
      </svg>
      {#if listOpen && suggestions.length > 0}
        <ul
          id="canister-scope-suggestions"
          role="listbox"
          class="absolute z-20 mt-1 w-full max-h-60 overflow-auto rounded-lg border border-[var(--color-border-primary)] bg-white shadow-lg py-1"
        >
          {#each suggestions as opt, i (opt.key)}
            <li role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={i === highlightIndex}
                class="w-full text-left px-3 py-2 text-sm transition-colors {i === highlightIndex
                  ? 'bg-primary-50 text-primary-900'
                  : 'text-primary-700 hover:bg-primary-50'}"
                onclick={() => add(opt.key)}
              >
                <span class="font-medium">{opt.name}</span>
                {#if opt.section || opt.stand}
                  <span class="block text-xs text-primary-500 truncate">
                    {[opt.section, opt.stand].filter(Boolean).join(' · ')}
                  </span>
                {/if}
                <span class="block text-[11px] text-primary-400 font-mono truncate">{opt.canister_id}</span>
              </button>
            </li>
          {/each}
        </ul>
      {:else if listOpen && query.trim() && suggestions.length === 0}
        <div
          class="absolute z-20 mt-1 w-full rounded-lg border border-[var(--color-border-primary)] bg-white shadow-lg px-3 py-2 text-sm text-primary-500"
        >
          No matching canisters in this time window.
        </div>
      {/if}
    </div>
    {#if selected.size > 0}
      <span class="text-xs text-primary-400">({selected.size} selected)</span>
    {:else}
      <span class="text-xs text-primary-400">(none — add canisters to plot)</span>
    {/if}
  </div>

  {#if selectedOptions.length > 0}
    <div class="flex flex-wrap gap-2">
      {#each selectedOptions as opt (opt.key)}
        {@const color = seriesColors.get(opt.name) ?? seriesColors.get(opt.key) ?? '#64748b'}
        <div
          class="inline-flex items-center gap-2 rounded-lg border border-[var(--color-border-primary)] bg-white px-2.5 py-1.5 text-xs text-primary-800 shadow-sm"
        >
          <span class="inline-block w-3 h-1.5 rounded-sm shrink-0" style="background:{color}"></span>
          <div class="min-w-0">
            <span class="font-medium truncate max-w-[180px] inline-block align-bottom">{opt.name}</span>
            {#if opt.section || opt.stand}
              <span class="block text-[10px] text-primary-500 truncate max-w-[220px]">
                {[opt.section, opt.stand].filter(Boolean).join(' · ')}
              </span>
            {/if}
          </div>
          <button
            type="button"
            class="shrink-0 rounded p-0.5 text-primary-400 hover:text-primary-700 hover:bg-primary-50"
            aria-label="Remove {opt.name} from chart"
            onclick={() => remove(opt.key)}
          >
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      {/each}
    </div>
  {/if}
</div>
