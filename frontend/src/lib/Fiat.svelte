<script lang="ts">
  // Small gray "≈ <currency>" annotation shown next to a raw cycle count.
  // A skeleton while the rate is in flight; nothing once the fetch finishes without one.
  import { cyclesToFiat, formatFiat } from '$lib/api';
  import { fx } from '$lib/fx.svelte';

  let {
    value,
    block = false,
    class: klass = '',
  }: { value: number | undefined | null; block?: boolean; class?: string } = $props();

  const label = $derived(formatFiat(cyclesToFiat(value, fx.microPerTcycle), fx.currency));
</script>

{#if fx.status === 'loading'}
  <span
    class="skeleton {block ? 'block h-3 w-16 mt-1' : 'inline-block h-3 w-14 ml-1 align-middle'} {klass}"
    role="status"
    aria-label="Loading value"
  ></span>
{:else if label}
  <span
    class="text-gray-400 font-normal tabular-nums {block ? 'block text-[11px]' : 'text-xs ml-1'} {klass}"
    title="≈ {label} · 1T cycles ≈ {formatFiat(fx.microPerTcycle / 1e6, fx.currency)} ({fx.currency})"
  >≈&nbsp;{label}</span>
{/if}
