<script lang="ts">
  import type { Permission } from '$lib/api';
  import { groupPermissions } from '$lib/governanceUx';

  interface Props {
    catalog: Permission[];
  }

  let { catalog }: Props = $props();

  const grouped = $derived(groupPermissions(catalog));
</script>

<div class="space-y-4">
  {#if catalog.length === 0}
    <p class="text-sm text-primary-400">No permissions returned from Casals.</p>
  {:else}
    {#each grouped as group (group.name)}
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-primary-400 mb-2">{group.name}</h3>
        <div class="overflow-x-auto rounded-lg border border-[var(--color-border-primary)]">
          <table class="w-full text-xs">
            <thead class="bg-primary-50 text-primary-500">
              <tr>
                <th class="text-left px-3 py-2 font-medium">Permission</th>
                <th class="text-left px-3 py-2 font-medium hidden sm:table-cell">Technical id</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-primary-50">
              {#each group.perms as perm (perm.key)}
                <tr class="hover:bg-primary-50/50">
                  <td class="px-3 py-2 text-primary-800">{perm.label}</td>
                  <td class="px-3 py-2 font-mono text-primary-400 hidden sm:table-cell">{perm.key}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/each}
    <p class="text-[11px] text-primary-400">
      Casals orchestration API permissions do not grant multisig signer status. Signers are configured on the
      <a href="/multisig" class="text-primary-600 underline">platform committee</a> canister.
    </p>
  {/if}
</div>
