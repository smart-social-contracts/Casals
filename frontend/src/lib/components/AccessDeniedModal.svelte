<script lang="ts">
  import { fade, scale } from 'svelte/transition';
  import { copyText } from '$lib/clipboard';
  import type { ClaimedSlot } from '$lib/api';

  interface Props {
    message: string;
    principal: string;
    onclose: () => void;
    /** Redeem an access code with the denied identity; resolves with the claimed slots. */
    onclaim?: (code: string) => Promise<ClaimedSlot[]>;
  }

  let { message, principal, onclose, onclaim }: Props = $props();

  let copied = $state(false);
  let code = $state('');
  let claiming = $state(false);
  let claimError = $state('');

  async function copyPrincipal() {
    if (!(await copyText(principal))) return;
    copied = true;
    setTimeout(() => {
      copied = false;
    }, 1500);
  }

  async function submitClaim(event?: Event) {
    event?.preventDefault();
    if (!onclaim || !code.trim() || claiming) return;
    claiming = true;
    claimError = '';
    try {
      await onclaim(code.trim());
      // On success the auth layer opens the session and clears the dialog.
    } catch (e: any) {
      claimError = e?.message ?? 'Failed to redeem the access code';
    } finally {
      claiming = false;
    }
  }
</script>

<div
  class="fixed inset-0 z-50 flex items-center justify-center"
  transition:fade={{ duration: 150 }}
  role="dialog"
  aria-modal="true"
  aria-labelledby="access-denied-title"
>
  <button
    type="button"
    class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
    aria-label="Close"
    onclick={onclose}
  ></button>
  <div
    class="relative bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6"
    transition:scale={{ start: 0.95, duration: 200 }}
  >
    <div class="flex items-start gap-3 mb-4">
      <div class="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
        <svg class="w-5 h-5 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
      </div>
      <div class="min-w-0">
        <h3 id="access-denied-title" class="text-lg font-semibold text-primary-900">Access Denied</h3>
        <p class="text-sm text-primary-500 mt-1">{message}</p>
      </div>
    </div>

    {#if onclaim}
      <form class="mb-5" onsubmit={submitClaim}>
        <label class="text-xs font-medium text-primary-500 mb-1.5 block" for="access-code">Access code</label>
        <div class="flex items-start gap-2">
          <input
            id="access-code"
            type="text"
            class="input flex-1 min-w-0 font-mono text-sm uppercase"
            placeholder="XXXXX-XXXXX-XXXXX-XXXXX"
            autocomplete="off"
            spellcheck="false"
            bind:value={code}
            disabled={claiming}
          />
          <button
            type="submit"
            class="btn-primary btn-sm shrink-0"
            disabled={claiming || !code.trim()}
          >
            {claiming ? 'Claiming…' : 'Claim access'}
          </button>
        </div>
        {#if claimError}
          <p class="text-xs text-red-600 mt-1.5">{claimError}</p>
        {:else}
          <p class="text-xs text-primary-400 mt-1.5">
            A code is single-use and binds this principal to the commander slot it was minted for.
          </p>
        {/if}
      </form>
    {/if}

    <div class="mb-6">
      <p class="text-xs font-medium text-primary-500 mb-1.5">Your principal</p>
      <div class="flex items-start gap-2">
        <div class="flex-1 min-w-0 rounded-lg border border-primary-200 bg-primary-50 px-3 py-2.5 font-mono text-sm text-primary-800 break-all">
          {principal}
        </div>
        <button
          type="button"
          class="btn-secondary btn-sm shrink-0 px-2 py-1 text-xs"
          aria-label="Copy principal"
          onclick={copyPrincipal}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>

    <div class="flex justify-end">
      <button type="button" class="btn-primary btn-sm" onclick={onclose} disabled={claiming}>Close</button>
    </div>
  </div>
</div>
