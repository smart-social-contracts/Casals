<script lang="ts">
  import { fade, scale } from 'svelte/transition';

  interface Props {
    message: string;
    principal: string;
    onclose: () => void;
  }

  let { message, principal, onclose }: Props = $props();
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

    <div class="mb-6">
      <p class="text-xs font-medium text-primary-500 mb-1.5">Your principal</p>
      <div class="rounded-lg border border-primary-200 bg-primary-50 px-3 py-2.5 font-mono text-sm text-primary-800 break-all">
        {principal}
      </div>
    </div>

    <div class="flex justify-end">
      <button type="button" class="btn-primary btn-sm" onclick={onclose}>Close</button>
    </div>
  </div>
</div>
