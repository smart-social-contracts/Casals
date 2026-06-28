<script lang="ts">
  import { treasuryAlert } from '$lib/stores/treasury';
</script>

{#if $treasuryAlert}
  <div
    class="border-b px-4 sm:px-6 py-2.5 flex items-start gap-3 {$treasuryAlert === 'critical'
      ? 'border-red-200 bg-red-50 text-red-900'
      : 'border-amber-200 bg-amber-50 text-amber-900'}"
    role="alert"
  >
    <svg
      class="w-5 h-5 shrink-0 mt-0.5 {$treasuryAlert === 'critical' ? 'text-red-600' : 'text-amber-600'}"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      stroke-width="2"
      aria-hidden="true"
    >
      {#if $treasuryAlert === 'critical'}
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      {:else}
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
      {/if}
    </svg>
    <div class="flex-1 min-w-0 text-sm leading-relaxed">
      {#if $treasuryAlert === 'critical'}
        <p>
          <span class="font-semibold">CRITICAL — treasury cycles dangerously low.</span>
          Balance is below 1&nbsp;TC. Top up immediately — without cycles, Casals cannot top up canisters,
          run autopilot, or keep the orchestra working.
        </p>
      {:else}
        <p>
          <span class="font-semibold">Low treasury cycles.</span>
          Balance is below 3&nbsp;TC. Top up soon — if the treasury runs out, Casals will not be able to work.
        </p>
      {/if}
    </div>
    <a
      href="/cycles"
      class="shrink-0 text-xs font-medium underline underline-offset-2 {$treasuryAlert === 'critical'
        ? 'text-red-800 hover:text-red-950'
        : 'text-amber-900 hover:text-amber-950'}"
    >Top up</a>
  </div>
{/if}
