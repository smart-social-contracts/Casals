<script lang="ts">
  import { toasts } from '$lib/stores/toast';
  import { fly, fade } from 'svelte/transition';
</script>

<div class="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
  {#each $toasts as toast (toast.id)}
    <div
      in:fly={{ x: 24, duration: 250 }}
      out:fade={{ duration: 150 }}
      class="card px-4 py-3 flex items-start gap-3 shadow-lg"
      role="alert"
    >
      <div class="shrink-0 mt-0.5">
        {#if toast.type === 'success'}
          <svg class="w-4 h-4 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        {:else if toast.type === 'error'}
          <svg class="w-4 h-4 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
          </svg>
        {:else}
          <svg class="w-4 h-4 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" />
          </svg>
        {/if}
      </div>
      <p class="text-sm text-primary-800 flex-1">{toast.message}</p>
      <button
        class="shrink-0 text-primary-400 hover:text-primary-700 transition-colors"
        onclick={() => toasts.dismiss(toast.id)}
        aria-label="Dismiss"
      >
        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  {/each}
</div>
