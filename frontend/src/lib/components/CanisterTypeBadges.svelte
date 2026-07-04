<script lang="ts">
  import { resolveWasmType, wasmTypeTags, wasmTypeBadgeClass } from '$lib/canisterTypes';
  import type { Canister } from '$lib/api';

  interface Props {
    canister: Pick<Canister, 'wasm_key' | 'wasm_type' | 'tags'>;
  }

  let { canister }: Props = $props();

  const tags = $derived(
    (canister.tags?.length ? canister.tags : wasmTypeTags(resolveWasmType(canister)))
      .filter((t) => t.toLowerCase() !== 'backend' && t.toLowerCase() !== 'frontend'),
  );
</script>

{#each tags as tag (tag)}
  <span class="badge {wasmTypeBadgeClass(tag)}">{tag}</span>
{/each}
