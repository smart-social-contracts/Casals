<script lang="ts">
  // Small gray "≈ <currency>" annotation shown next to a raw cycle count.
  // Renders nothing until a conversion rate has been fetched.
  import { cyclesToFiat, formatFiat } from '$lib/api';
  import { fx } from '$lib/fx.svelte';

  let {
    value,
    block = false,
    class: klass = '',
  }: { value: number | undefined | null; block?: boolean; class?: string } = $props();

  const label = $derived(formatFiat(cyclesToFiat(value, fx.microPerTcycle), fx.currency));
</script>

{#if label}
  <span
    class="text-gray-400 font-normal tabular-nums {block ? 'block text-[11px]' : 'text-xs ml-1'} {klass}"
    title="≈ {label} · 1T cycles ≈ {formatFiat(fx.microPerTcycle / 1e6, fx.currency)} ({fx.currency})"
  >≈&nbsp;{label}</span>
{/if}
