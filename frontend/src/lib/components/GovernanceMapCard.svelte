<script lang="ts">
  import { GOVERNANCE_MAP_LAYERS } from '$lib/governanceUx';

  interface Props {
    compact?: boolean;
  }

  let { compact = false }: Props = $props();
</script>

<div class="card p-4 space-y-3 {compact ? '' : ''}">
  <div>
    <h2 class="text-sm font-semibold text-primary-900">Governance map</h2>
    <p class="text-xs text-primary-500 mt-1">
      Five layers — access, Casals approvals, arrangement runners, platform committee, Baton.
    </p>
  </div>
  <ol class="space-y-2 text-xs text-primary-600 list-decimal list-inside">
    {#each GOVERNANCE_MAP_LAYERS as layer (layer.title)}
      <li>
        <span class="font-medium text-primary-800">{layer.title}</span>
        — {layer.body}
      </li>
    {/each}
  </ol>
</div>
