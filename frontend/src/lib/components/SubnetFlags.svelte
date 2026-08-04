<script lang="ts">
  import {
    getSubnetGeo,
    resolveCanisterSubnet,
    subnetGeoTitle,
    subnetPrefix,
    type SubnetGeo,
  } from '$lib/subnetGeo';
  import { copyText } from '$lib/clipboard';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    subnetId?: string;
    canisterId?: string;
    size?: 'sm' | 'md';
    /** When true, flags appear in a tooltip on hover of the parent `group/subnet` container. */
    hoverOnly?: boolean;
    /** `badge` — orchestra canister chip; `inline` — plain prefix + hover; default — flags only. */
    variant?: 'flags-only' | 'badge' | 'inline';
  }

  let {
    subnetId = '',
    canisterId = '',
    size = 'sm',
    hoverOnly = false,
    variant = 'flags-only',
  }: Props = $props();

  let geo = $state<SubnetGeo | null>(null);
  let resolvedSubnetId = $state('');
  let loading = $state(false);
  let copied = $state(false);

  $effect(() => {
    const sid = (subnetId || '').trim();
    const cid = (canisterId || '').trim();
    if (!sid && !cid) {
      geo = null;
      resolvedSubnetId = '';
      loading = false;
      return;
    }

    let cancelled = false;
    loading = true;
    resolvedSubnetId = sid;
    (async () => {
      try {
        let resolved = sid;
        if (!resolved && cid) {
          resolved = (await resolveCanisterSubnet(cid)) ?? '';
        }
        if (!cancelled) resolvedSubnetId = resolved;
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
  const labelPrefix = $derived(
    resolvedSubnetId ? subnetPrefix(resolvedSubnetId) : (loading ? '…' : '—'),
  );
  const showHoverPanel = $derived(Boolean(resolvedSubnetId || geo?.orderedCountries.length));

  async function copySubnetId(e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!resolvedSubnetId) return;
    if (await copyText(resolvedSubnetId)) {
      copied = true;
      toasts.success('Copied subnet ID');
      setTimeout(() => {
        copied = false;
      }, 1500);
    }
  }
</script>

{#snippet hoverPanel()}
  {#if showHoverPanel}
    <span
      class="subnet-flags-hover absolute left-0 top-full z-20 mt-0.5 hidden group-hover/subnet:flex flex-col gap-1 rounded-md border border-[var(--color-border-primary)] bg-white px-1.5 py-1 shadow-md min-w-[11rem] max-w-[20rem]"
      title={title || undefined}
      aria-label={title || (resolvedSubnetId ? `subnet ${resolvedSubnetId}` : 'subnet geography')}
    >
      {#if resolvedSubnetId}
        <div class="flex items-center gap-1 min-w-0">
          <span class="font-mono text-[11px] text-primary-800 truncate flex-1" title={resolvedSubnetId}>
            {resolvedSubnetId}
          </span>
          <button
            type="button"
            class="icon-btn shrink-0 {copied ? 'text-emerald-600' : ''}"
            aria-label="Copy subnet ID"
            title="Copy subnet ID"
            onclick={copySubnetId}
          >
            {#if copied}
              <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
            {:else}
              <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3" />
              </svg>
            {/if}
          </button>
        </div>
      {/if}
      {#if geo?.orderedCountries.length}
        <div class="flex flex-wrap items-center gap-0.5 {flagClass}">
          {#each geo.orderedCountries as country (country.code)}
            <span class="subnet-flag" title="{country.region}: {country.name}">{country.flag}</span>
          {/each}
        </div>
      {/if}
    </span>
  {/if}
{/snippet}

{#if variant === 'badge'}
  <span
    class="badge badge-neutral font-mono inline-flex items-center gap-1 group/subnet relative"
    title={resolvedSubnetId ? `subnet ${resolvedSubnetId}` : 'subnet lookup…'}
  >
    ⬡ {labelPrefix}
    {@render hoverPanel()}
  </span>
{:else if variant === 'inline'}
  <span
    class="font-mono inline-flex items-center gap-1 group/subnet relative w-fit"
    title={resolvedSubnetId ? `subnet ${resolvedSubnetId}` : 'subnet lookup…'}
  >
    ⬡ {labelPrefix}
    {@render hoverPanel()}
  </span>
{:else if loading && !geo && !hoverOnly}
  <span class="text-primary-300 {flagClass}" aria-hidden="true">…</span>
{:else if showHoverPanel && hoverOnly}
  {@render hoverPanel()}
{:else if geo?.orderedCountries.length}
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

<style>
  .subnet-flag {
    display: inline-block;
  }
</style>
