<script lang="ts">
  import { onDestroy } from 'svelte';
  import { copyText } from '$lib/clipboard';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    text: string;
    /** What is copied, e.g. "ICP account"; names the button for screen readers. */
    label: string;
  }

  let { text, label }: Props = $props();

  let copied = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;

  onDestroy(() => clearTimeout(timer));

  async function copy() {
    if (!(await copyText(text))) {
      toasts.error('Copy failed');
      return;
    }
    copied = true;
    // A second click restarts the full 1.5 s instead of cutting it short.
    clearTimeout(timer);
    timer = setTimeout(() => {
      copied = false;
    }, 1500);
  }
</script>

<button
  type="button"
  class="icon-btn shrink-0 {copied ? '!text-emerald-600' : ''}"
  aria-label={copied ? 'Copied' : `Copy ${label}`}
  onclick={copy}
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
