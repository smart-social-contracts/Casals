<script lang="ts">
  import { fade, scale } from 'svelte/transition';

  export interface FieldOption {
    value: string;
    label: string;
  }

  export interface Field {
    name: string;
    label: string;
    type?: 'text' | 'textarea' | 'select' | 'checkbox';
    placeholder?: string;
    required?: boolean;
    options?: FieldOption[];
    value?: string | boolean;
    help?: string;
  }

  type Values = Record<string, string | boolean>;

  interface Props {
    title?: string;
    description?: string;
    fields?: Field[];
    submitLabel?: string;
    busy?: boolean;
    logLines?: string[];
    onsubmit?: (values: Values) => void;
    oncancel?: () => void;
  }

  let {
    title = '',
    description = '',
    fields = [],
    submitLabel = 'Submit',
    busy = false,
    logLines = [],
    onsubmit,
    oncancel,
  }: Props = $props();

  let logEl = $state<HTMLElement | null>(null);

  $effect(() => {
    // Auto-scroll to bottom whenever new log lines arrive.
    if (logLines.length && logEl) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  });

  // Initialized once at mount. The parent mounts a fresh modal each time it is
  // opened, so the form always starts from the supplied field defaults.
  function initValues(): Values {
    const next: Values = {};
    for (const f of fields) {
      next[f.name] = f.value ?? (f.type === 'checkbox' ? false : '');
    }
    return next;
  }

  let values = $state<Values>(initValues());

  function cancel() {
    oncancel?.();
  }

  function submit(event: Event) {
    event.preventDefault();
    if (busy) return;
    onsubmit?.({ ...values });
  }
</script>

<div
  class="fixed inset-0 z-40 flex items-center justify-center"
  transition:fade={{ duration: 150 }}
>
  <button
    type="button"
    class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
    aria-label="Close"
    onclick={cancel}
  ></button>
  <div
    class="relative bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6 max-h-[90vh] overflow-y-auto"
    transition:scale={{ start: 0.95, duration: 200 }}
  >
    <h3 class="text-lg font-semibold text-primary-900 mb-1">{title}</h3>
    {#if description}
      <p class="text-sm text-primary-500 mb-4">{description}</p>
    {:else}
      <div class="mb-4"></div>
    {/if}

    <form class="space-y-4" onsubmit={submit}>
      {#each fields as field (field.name)}
        <div>
          {#if field.type === 'checkbox'}
            <label class="flex items-start gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                class="mt-0.5 w-4 h-4 rounded border-primary-300 accent-red-600"
                bind:checked={values[field.name] as boolean}
              />
              <span class="text-sm font-medium {values[field.name] ? 'text-red-700' : 'text-primary-700'}">{field.label}</span>
            </label>
            {#if field.help && values[field.name]}
              <div class="mt-1.5 flex items-start gap-1.5 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
                {field.help}
              </div>
            {/if}
          {:else}
            <label class="label" for={`field-${field.name}`}>
              {field.label}{#if field.required}<span class="text-red-500"> *</span>{/if}
            </label>
            {#if field.type === 'textarea'}
              <textarea
                id={`field-${field.name}`}
                class="input"
                rows="3"
                placeholder={field.placeholder ?? ''}
                bind:value={values[field.name] as string}
              ></textarea>
            {:else if field.type === 'select'}
              <select id={`field-${field.name}`} class="input" bind:value={values[field.name] as string}>
                {#each field.options ?? [] as opt (opt.value)}
                  <option value={opt.value}>{opt.label}</option>
                {/each}
              </select>
            {:else}
              <input
                id={`field-${field.name}`}
                type="text"
                class="input"
                placeholder={field.placeholder ?? ''}
                bind:value={values[field.name] as string}
              />
            {/if}
          {/if}
          {#if field.help}
            <p class="text-xs text-primary-400 mt-1">{field.help}</p>
          {/if}
        </div>
      {/each}

      {#if busy}
        <div class="rounded-lg bg-gray-950 border border-gray-800 overflow-hidden">
          {#if logLines.length > 0}
            <div
              bind:this={logEl}
              class="p-3 h-40 overflow-y-auto font-mono text-xs text-green-400 space-y-0.5"
            >
              {#each logLines as line}
                <div class="leading-snug whitespace-pre-wrap break-all">{line}</div>
              {/each}
              <div class="animate-pulse text-gray-500">▌</div>
            </div>
          {:else}
            <div class="p-3 flex items-center gap-2 font-mono text-xs text-green-400">
              <svg class="w-3 h-3 animate-spin shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              Waiting for first event…
            </div>
          {/if}
          <div class="border-t border-gray-800 px-3 py-1.5 text-[10px] text-gray-500">
            Running on-chain · you can close this window safely
          </div>
        </div>
      {/if}

      <div class="flex items-center justify-end gap-3 pt-2">
        <button type="button" class="btn-secondary btn-sm" onclick={cancel}>
          {busy ? 'Close' : 'Cancel'}
        </button>
        <button
          type="submit"
          class="{(values['reinstall'] ? 'bg-red-600 hover:bg-red-700 focus:ring-red-500' : '') + ' btn-primary btn-sm'}"
          disabled={busy}
        >
          {#if busy}
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            Working…
          {:else}
            {submitLabel}
          {/if}
        </button>
      </div>
    </form>
  </div>
</div>
