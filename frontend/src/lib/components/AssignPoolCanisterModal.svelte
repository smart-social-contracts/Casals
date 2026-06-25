<script lang="ts">
  import FormModal from '$lib/components/FormModal.svelte';
  import type { Field } from '$lib/components/FormModal.svelte';
  import {
    assignPoolCanister,
    createStand,
    getTree,
    listAuthorizedWasms,
    shortPrincipal,
    type CanisterKind,
    type Tree,
  } from '$lib/api';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    canisterId: string;
    onsuccess?: () => void;
    oncancel?: () => void;
  }

  let { canisterId, onsuccess, oncancel }: Props = $props();

  let tree = $state<Tree | null>(null);
  let wasmFamilies = $state<string[]>([]);
  let loading = $state(true);
  let busy = $state(false);
  let loadError = $state('');

  $effect(() => {
    let cancelled = false;
    loading = true;
    loadError = '';
    (async () => {
      try {
        const [t, wasms] = await Promise.all([
          getTree(),
          listAuthorizedWasms(),
        ]);
        if (cancelled) return;
        tree = t;
        const fams = new Set<string>();
        for (const w of wasms) {
          if (w.family) fams.add(w.family);
        }
        wasmFamilies = [...fams].sort();
      } catch (e) {
        if (!cancelled) loadError = e instanceof Error ? e.message : String(e);
      } finally {
        if (!cancelled) loading = false;
      }
    })();
    return () => { cancelled = true; };
  });

  const standOptions = $derived.by(() => {
    if (!tree) return [];
    return tree.sections.flatMap((sec) =>
      sec.stands.map((st) => ({
        value: st.name,
        label: `${sec.name} / ${st.name}`,
      })),
    );
  });

  const sectionOptions = $derived.by(() =>
    (tree?.sections ?? []).map((sec) => ({ value: sec.name, label: sec.name })),
  );

  const defaultSection = $derived.by(() => {
    const names = sectionOptions.map((o) => o.value);
    if (names.includes('Demo')) return 'Demo';
    return names[0] ?? '';
  });

  const needsNewStand = $derived(standOptions.length === 0);

  const fields = $derived.by((): Field[] => {
    const wasmOptions = [
      { value: '', label: 'Keep existing code (register only)' },
      ...wasmFamilies.map((f) => ({ value: f, label: f })),
    ];

    const placement: Field[] = needsNewStand
      ? [
          {
            name: 'section',
            label: 'Section',
            type: 'select',
            required: true,
            value: defaultSection,
            options: sectionOptions,
            help: sectionOptions.length === 0
              ? 'Create a section on the Orchestra page first.'
              : 'A new stand will be created in this section when you assign.',
          },
          {
            name: 'new_stand',
            label: 'Stand name',
            required: true,
            placeholder: 'Motoko',
            help: 'New stand to hold this canister (e.g. Motoko, Rust, Python).',
          },
        ]
      : [
          {
            name: 'stand',
            label: 'Section / stand',
            type: 'select',
            required: true,
            value: standOptions[0]?.value ?? '',
            options: standOptions,
          },
        ];

    return [
      ...placement,
      {
        name: 'name',
        label: 'Canister name',
        required: true,
        placeholder: 'motoko-backend',
      },
      {
        name: 'kind',
        label: 'Kind',
        type: 'select',
        value: 'backend',
        options: [
          { value: 'backend', label: 'Backend' },
          { value: 'frontend', label: 'Frontend' },
        ],
      },
      {
        name: 'wasm_key',
        label: 'WASM (optional)',
        type: 'select',
        value: '',
        options: wasmOptions,
        help: 'Reinstall an authorized WASM, or leave as register-only to keep what is already on the canister.',
      },
    ];
  });

  const canSubmit = $derived.by(() => {
    if (needsNewStand) {
      return sectionOptions.length > 0;
    }
    return standOptions.length > 0;
  });

  async function resolveStandName(values: Record<string, string | boolean>): Promise<string> {
    if (!needsNewStand) {
      const stand = String(values.stand ?? '').trim();
      if (!stand) throw new Error('Stand required');
      return stand;
    }
    const section = String(values.section ?? '').trim();
    const newStand = String(values.new_stand ?? '').trim();
    if (!section) throw new Error('Section required');
    if (!newStand) throw new Error('Stand name required');
    const created = await createStand({ section, name: newStand });
    if (!created.ok) throw new Error(created.error ?? 'Failed to create stand');
    return newStand;
  }

  async function submit(values: Record<string, string | boolean>) {
    if (busy || !canSubmit) return;
    busy = true;
    try {
      const stand = await resolveStandName(values);
      const wasmKey = String(values.wasm_key ?? '').trim();
      const res = await assignPoolCanister({
        canister_id: canisterId,
        stand,
        name: String(values.name).trim(),
        kind: String(values.kind).trim() as CanisterKind,
        ...(wasmKey ? { wasm_key: wasmKey } : {}),
      });
      if (!res.ok) throw new Error(res.error ?? 'Assign failed');
      toasts.success(`Assigned ${shortPrincipal(canisterId)} to ${values.name}`);
      onsuccess?.();
    } catch (e) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }
</script>

{#if loading}
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-black/30">
    <div class="card p-6 text-sm text-primary-500">Loading orchestra…</div>
  </div>
{:else if loadError}
  <FormModal
    title="Assign pooled canister"
    description="Could not load the orchestra tree."
    fields={[]}
    submitLabel="Close"
    onsubmit={() => oncancel?.()}
    oncancel={() => oncancel?.()}
  />
{:else}
  {#key `${needsNewStand}-${defaultSection}-${standOptions.length}`}
    <FormModal
      title="Assign to section / stand"
      description="Link pool canister {shortPrincipal(canisterId)} into the orchestra."
      {fields}
      submitLabel={busy ? 'Assigning…' : 'Assign'}
      busy={busy}
      submitDisabled={!canSubmit}
      onsubmit={submit}
      oncancel={() => oncancel?.()}
    />
  {/key}
{/if}
