<script lang="ts">
  import type { RegionFlagGroup } from '$lib/subnetGeo';

  interface Props {
    groups: RegionFlagGroup[];
    /** ISO country codes present in this subnet; others render grayed out. */
    presentCodes?: Iterable<string>;
    size?: 'sm' | 'md';
    /** ``labels`` = region headers only (table thead); ``flags`` = flag row (table body). */
    variant?: 'flags' | 'labels';
  }

  let {
    groups,
    presentCodes = [],
    size = 'md',
    variant = 'flags',
  }: Props = $props();

  const present = $derived(new Set([...presentCodes].map((c) => c.toUpperCase())));

  function isPresent(code: string): boolean {
    return present.has(code.toUpperCase());
  }

  const flagSizeClass = $derived(size === 'md' ? 'text-base' : 'text-sm');
</script>

<div class="subnet-flag-matrix inline-flex min-w-0">
  {#if variant === 'labels'}
    <div class="inline-flex items-end gap-0" aria-hidden="true">
      {#each groups as group, gi (group.region)}
        {#if gi > 0}
          <span class="region-sep region-sep-header"></span>
        {/if}
        <div class="region-group">
          <div class="region-label">{group.label}</div>
          <div class="inline-flex">
            {#each group.countries as country (country.code)}
              <span class="flag-cell {flagSizeClass} invisible" aria-hidden="true">{country.flag}</span>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  {:else}
    <div class="inline-flex items-center gap-0" role="img">
      {#each groups as group, gi (group.region)}
        {#if gi > 0}
          <span class="region-sep" aria-hidden="true"></span>
        {/if}
        <div class="region-group inline-flex items-center">
          {#each group.countries as country (country.code)}
            {@const active = isPresent(country.code)}
            <span
              class="flag-cell {flagSizeClass} {active ? 'flag-active' : 'flag-inactive'}"
              title="{active ? `${group.label}: ${country.name}` : `${country.name} (not in this subnet)`}"
            >{country.flag}</span>
          {/each}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .region-group {
    flex-shrink: 0;
  }

  .region-label {
    font-size: 9px;
    line-height: 1;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--color-text-primary, #94a3b8);
    text-align: center;
    padding-bottom: 2px;
    white-space: nowrap;
  }

  .region-sep {
    width: 1px;
    align-self: stretch;
    margin: 0 5px;
    background: var(--color-border-primary, #e2e8f0);
    flex-shrink: 0;
  }

  .region-sep-header {
    margin-bottom: 2px;
  }

  .flag-cell {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.125rem;
    line-height: 1;
    flex-shrink: 0;
  }

  .flag-active {
    opacity: 1;
  }

  .flag-inactive {
    opacity: 0.18;
    filter: grayscale(1);
  }
</style>
