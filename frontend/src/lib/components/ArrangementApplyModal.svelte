<script lang="ts">
  import type { ArrangementParameterSpec } from '$lib/arrangement-params';
  import { buildApplyParameters, validateApplyForm } from '$lib/arrangement-params';

  interface Props {
    open: boolean;
    arrangementName: string;
    schema: Record<string, ArrangementParameterSpec>;
    defaults?: Record<string, unknown>;
    onClose: () => void;
    onSubmit: (parameters: Record<string, unknown>) => void | Promise<void>;
  }

  let {
    open = false,
    arrangementName,
    schema,
    defaults = {},
    onClose,
    onSubmit,
  }: Props = $props();

  let values = $state<Record<string, string | boolean>>({});
  let busy = $state(false);
  let error = $state('');

  const fields = $derived(
    Object.entries(schema).map(([key, spec]) => ({ key, spec })),
  );

  $effect(() => {
    if (!open) return;
    const next: Record<string, string | boolean> = {};
    for (const [key, spec] of Object.entries(schema)) {
      const d = defaults[key];
      if (spec.type === 'bool') {
        next[key] = typeof d === 'boolean' ? d : false;
      } else if (d !== undefined && d !== null && spec.type !== 'sha256') {
        next[key] = String(d);
      } else {
        next[key] = '';
      }
    }
    values = next;
    error = '';
    busy = false;
  });

  async function submit() {
    error = validateApplyForm(schema, values);
    if (error) return;
    busy = true;
    try {
      const parameters = await buildApplyParameters(schema, values);
      await onSubmit(parameters);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

{#if open}
  <div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" role="presentation">
    <button
      type="button"
      class="absolute inset-0 cursor-default"
      aria-label="Close apply dialog"
      onclick={onClose}
    ></button>
    <div
      class="relative w-full max-w-lg card p-5 shadow-xl"
      role="dialog"
      aria-modal="true"
      aria-labelledby="apply-modal-title"
    >
      <h2 id="apply-modal-title" class="text-lg font-semibold text-primary-900">
        Apply "{arrangementName}"
      </h2>
      <p class="text-xs text-primary-500 mt-1">
        Enter parameter values for this run. They are merged with saved defaults and substituted into step args.
      </p>

      <div class="mt-4 space-y-4 max-h-[50vh] overflow-y-auto pr-1">
        {#each fields as field (field.key)}
          <label class="block space-y-1">
            <span class="text-sm font-medium text-primary-800">
              {field.spec.label || field.key}
              {#if field.spec.required}
                <span class="text-red-600">*</span>
              {/if}
            </span>
            {#if field.spec.description}
              <span class="block text-xs text-primary-400">{field.spec.description}</span>
            {/if}
            {#if field.spec.type === 'bool'}
              <input
                type="checkbox"
                class="mt-1"
                checked={!!values[field.key]}
                onchange={(e) => {
                  values = { ...values, [field.key]: (e.currentTarget as HTMLInputElement).checked };
                }}
              />
            {:else}
              <input
                type={field.spec.type === 'number' ? 'number' : 'text'}
                class="w-full mt-1 px-3 py-2 text-sm rounded-lg border border-[var(--color-border-primary)] font-mono"
                placeholder={field.spec.type === 'sha256' ? 'Plaintext value (hashed on submit)' : ''}
                value={String(values[field.key] ?? '')}
                oninput={(e) => {
                  values = { ...values, [field.key]: (e.currentTarget as HTMLInputElement).value };
                }}
              />
            {/if}
          </label>
        {/each}
        {#if fields.length === 0}
          <p class="text-sm text-primary-500">No parameters required for this arrangement.</p>
        {/if}
      </div>

      {#if error}
        <p class="text-xs text-red-600 mt-3">{error}</p>
      {/if}

      <div class="mt-5 flex justify-end gap-2">
        <button type="button" class="btn-secondary btn-sm" onclick={onClose} disabled={busy}>Cancel</button>
        <button type="button" class="btn-primary btn-sm" onclick={submit} disabled={busy}>
          {busy ? 'Applying…' : 'Apply'}
        </button>
      </div>
    </div>
  </div>
{/if}
