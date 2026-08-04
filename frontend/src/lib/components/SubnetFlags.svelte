<script lang="ts">
  import {
    getSubnetGeo,
    resolveCanisterSubnet,
    subnetGeoTitle,
    type SubnetGeo,
  } from '$lib/subnetGeo';

  interface Props {
    subnetId?: string;
    canisterId?: string;
    size?: 'sm' | 'md';
    /** When true, flags appear in a tooltip on hover of the parent `group/subnet` container. */
    hoverOnly?: boolean;
  }

  let { subnetId = '', canisterId = '', size = 'sm', hoverOnly = false }: Props = $props();

  let geo = $state<SubnetGeo | null>(null);
  let loading = $state(false);

  $effect(() => {
    const sid = (subnetId || '').trim();
    const cid = (canisterId || '').trim();
    if (!sid && !cid) {
      geo = null;
      return;
    }

    let cancelled = false;
    loading = true;
    (async () => {
      try {
        let resolved = sid;
        if (!resolved && cid) {
          resolved = (await resolveCanisterSubnet(cid)) ?? '';
        }
        if (!resolved) {
          if (!cancelled) geo = null;
          return;
        }
        const g = await getSubnetGeo(resolved);
        if (!cancelled) geo = g;
      } catch {
        if (!cancelled) geo = null;
      } finally {
        if (!cancelled) loading = false;
      }
    })();

    return () => {
      cancelled = true;
    };
  });

  const title = $derived(subnetGeoTitle(geo));
  const flagClass = $derived(size === 'md' ? 'text-base leading-none' : 'text-sm leading-none');
</script>

{#if loading && !geo && !hoverOnly}
  <span class="text-primary-300 {flagClass}" aria-hidden="true">…</span>
{:else if geo?.orderedCountries.length}
  {#if hoverOnly}
    <span
      class="subnet-flags-hover absolute left-0 top-full z-20 mt-0.5 hidden group-hover/subnet:flex flex-wrap items-center gap-0.5 rounded-md border border-[var(--color-border-primary)] bg-white px-1.5 py-1 shadow-md {flagClass}"
      {title}
      aria-label={title}
    >
      {#each geo.orderedCountries as country (country.code)}
        <span class="subnet-flag" title="{country.region}: {country.name}">{country.flag}</span>
      {/each}
    </span>
  {:else}
    <span
      class="inline-flex flex-wrap items-center gap-0.5 {flagClass}"
      {title}
      aria-label={title}
    >
      {#each geo.orderedCountries as country (country.code)}
        <span class="subnet-flag" title="{country.region}: {country.name}">{country.flag}</span>
      {/each}
    </span>
  {/if}
{/if}

<style>
  .subnet-flag {
    display: inline-block;
  }
</style>
