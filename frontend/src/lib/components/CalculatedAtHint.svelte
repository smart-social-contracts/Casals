<script lang="ts">
  import { formatCalculatedAt } from '$lib/api';

  interface Props {
    at?: number | null;
    label?: string;
    class?: string;
  }

  let { at = null, label = 'Last calculated', class: className = '' }: Props = $props();

  const text = $derived(formatCalculatedAt(at));
</script>

{#if text}
  <span class="relative inline-flex align-middle group {className}">
    <button
      type="button"
      class="p-0.5 rounded-full text-primary-400 hover:text-primary-700 hover:bg-primary-100/80 transition-colors"
      aria-label="{label}: {text}"
    >
      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <circle cx="12" cy="12" r="10" />
        <path stroke-linecap="round" d="M12 16v-4m0-4h.01" />
      </svg>
    </button>
    <span
      class="invisible opacity-0 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100 absolute z-30 left-1/2 -translate-x-1/2 bottom-full mb-1.5 w-max max-w-[18rem] rounded-md border border-[var(--color-border-primary)] bg-white px-2.5 py-1.5 text-[11px] leading-snug text-primary-700 shadow-lg pointer-events-none transition-opacity"
      role="tooltip"
    >
      <span class="block text-[10px] font-medium uppercase tracking-wide text-primary-500">{label}</span>
      <span class="block mt-0.5">{text}</span>
    </span>
  </span>
{/if}
