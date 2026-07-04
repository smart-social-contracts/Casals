<script lang="ts">
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  interface Props {
    canisterId?: string;
    controllers?: string[];
    inline?: boolean;
  }

  let { canisterId = '', controllers = [], inline = false }: Props = $props();

  let open = $state(false);

  const count = $derived(controllers.length);

  async function copy(text: string) {
    if (await copyText(text)) toasts.success('Copied');
    else toasts.error('Copy failed');
  }

  function toggle(e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!count) return;
    open = !open;
  }

  function close() {
    open = false;
  }
</script>

<svelte:window onclick={close} />

{#if inline}
  <div class="space-y-1">
    <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-500">Controllers</p>
    {#if count === 0}
      <p class="text-xs text-primary-400">None cached — refresh Orchestra to load from IC.</p>
    {:else}
      <ul class="space-y-1">
        {#each controllers as principal (principal)}
          <li class="flex items-center gap-2 min-w-0">
            <code class="text-[11px] font-mono text-primary-800 truncate flex-1" title={principal}>
              {principal}
            </code>
            <button
              type="button"
              class="shrink-0 p-1 rounded hover:bg-primary-50 text-primary-500"
              title="Copy principal"
              onclick={() => copy(principal)}
            >
              <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3" />
              </svg>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
{:else}
  <span class="relative inline-flex">
    <button
      type="button"
      class="badge badge-neutral hover:bg-primary-100 transition-colors cursor-pointer {count === 0 ? 'opacity-60' : ''}"
      title={count ? 'IC controllers — click to list' : 'No controllers cached'}
      onclick={toggle}
      disabled={count === 0}
    >
      {count} controller{count === 1 ? '' : 's'}
    </button>
    {#if open && count > 0}
      <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
      <div
        class="absolute left-0 top-full z-40 mt-1 min-w-[18rem] max-w-[24rem] rounded-lg border border-[var(--color-border-primary)] bg-white shadow-lg p-3 text-left"
        onclick={(e) => e.stopPropagation()}
      >
        <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-500 mb-2">
          IC controllers
        </p>
        <ul class="space-y-1.5">
          {#each controllers as principal (principal)}
            <li class="flex items-center gap-2 min-w-0">
              <code class="text-[11px] font-mono text-primary-800 truncate flex-1" title={principal}>
                {principal}
              </code>
              <button
                type="button"
                class="shrink-0 p-1 rounded hover:bg-primary-50 text-primary-500"
                title="Copy principal"
                onclick={() => copy(principal)}
              >
                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3" />
                </svg>
              </button>
            </li>
          {/each}
        </ul>
        {#if canisterId}
          <p class="mt-2 pt-2 border-t border-[var(--color-border-primary)] text-[10px] text-primary-400 font-mono truncate">
            {canisterId}
          </p>
        {/if}
      </div>
    {/if}
  </span>
{/if}
