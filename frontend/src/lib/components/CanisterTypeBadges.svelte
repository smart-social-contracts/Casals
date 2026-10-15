<script lang="ts">
  import { resolveWasmType, wasmTypeTags, wasmTypeBadgeClass } from '$lib/canisterTypes';
  import type { CanisterRow } from '$lib/api';

  interface Props {
    canister: Pick<CanisterRow, 'wasm' | 'tags'>;
  }

  let { canister }: Props = $props();

  const wasmTags = $derived(wasmTypeTags(resolveWasmType(canister)));
  const userTags = $derived(canister.tags ?? []);
</script>

{#each wasmTags as tag (tag)}
  <span class="badge {wasmTypeBadgeClass(tag)}">{tag}</span>
{/each}
{#each userTags as tag (`user-${tag}`)}
  <span class="badge badge-user-tag" title="Commander tag">{tag}</span>
{/each}
