<script lang="ts">
  import type { Canister } from '$lib/api';
  import type { BatonRef } from '$lib/orchestraGovernance';
  import { canisterGovernanceMeta } from '$lib/orchestraGovernance';
  import CanisterTypeBadges from '$lib/components/CanisterTypeBadges.svelte';
  import CanisterControllersBadge from '$lib/components/CanisterControllersBadge.svelte';
  import { batonConsoleUrl } from '$lib/orchestrationNav';
  import type { Tree } from '$lib/api';

  interface Props {
    canister: Canister;
    tree?: Tree | null;
    batons?: BatonRef[];
    principalLabels?: Map<string, string>;
    /** compact = badge popover; full = inline list; hide controllers in summary row */
    showControllers?: boolean;
    mode?: 'compact' | 'full';
    /** inline = sit in a parent flex row (tree summary) */
    inline?: boolean;
  }

  let {
    canister,
    tree = null,
    batons = [],
    principalLabels = new Map(),
    mode = 'compact',
    showControllers = true,
    inline = false,
  }: Props = $props();

  const meta = $derived(canisterGovernanceMeta(canister, batons, tree));
</script>

<div class={inline ? 'inline-flex items-center gap-1.5 flex-wrap' : 'space-y-1.5'}>
  <div class="flex items-center gap-1.5 flex-wrap">
    <CanisterTypeBadges {canister} />
    {#if meta.managedBy}
      <a
        href={batonConsoleUrl(meta.managedBy.canister_id)}
        class="badge badge-wasm-baton hover:opacity-90"
        title="Managed by {meta.managedBy.name}"
      >
        managed · {meta.managedBy.name}
      </a>
      {#if meta.batonIsController}
        <span class="badge badge-ok text-[10px]">baton IC controller</span>
      {:else if meta.managedBy}
        <span class="badge badge-warn text-[10px]">baton not IC controller</span>
      {/if}
    {/if}
  </div>

  {#if showControllers}
    {#if mode === 'full'}
      <CanisterControllersBadge
        canisterId={canister.canister_id}
        controllers={canister.controllers}
        {principalLabels}
        inline
      />
    {:else}
      <CanisterControllersBadge
        canisterId={canister.canister_id}
        controllers={canister.controllers}
        {principalLabels}
      />
    {/if}
  {/if}
</div>
