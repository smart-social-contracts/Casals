<script lang="ts">
  import { resolveWasmType, wasmTypeTags, wasmTypeBadgeClass } from '$lib/canisterTypes';
  import type { Canister } from '$lib/api';

  interface Props {
    canister: Pick<Canister, 'wasm_key' | 'wasm_type' | 'tags' | 'user_tags'>;
  }

  let { canister }: Props = $props();

  const wasmTags = $derived(
    (canister.tags?.length ? canister.tags : wasmTypeTags(resolveWasmType(canister)))
      .filter((t) => t.toLowerCase() !== 'backend' && t.toLowerCase() !== 'frontend'),
  );

  const userTags = $derived(canister.user_tags ?? []);
</script>

{#each wasmTags as tag (tag)}
  <span class="badge {wasmTypeBadgeClass(tag)}">{tag}</span>
{/each}
{#each userTags as tag (`user-${tag}`)}
  <span class="badge badge-user-tag" title="Commander tag">{tag}</span>
{/each}
