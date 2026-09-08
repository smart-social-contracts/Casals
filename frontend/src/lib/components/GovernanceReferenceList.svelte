<script lang="ts">
  import type { ReferenceEntry } from '$lib/governanceUx';

  interface Props {
    title: string;
    intro: string;
    entries: ReferenceEntry[];
  }

  let { title, intro, entries }: Props = $props();
</script>

<section class="card p-4 space-y-3">
  <div>
    <h2 class="text-sm font-semibold text-primary-900">{title}</h2>
    <p class="text-xs text-primary-500 mt-1">{intro}</p>
  </div>
  <ul class="space-y-2 text-xs">
    {#each entries as entry (entry.id)}
      <li class="border border-primary-100 rounded-lg px-3 py-2">
        <div class="font-medium text-primary-800">{entry.label}</div>
        <div class="text-primary-500 mt-0.5">{entry.description}</div>
        <div class="font-mono text-[10px] text-primary-400 mt-1">{entry.id}</div>
      </li>
    {/each}
  </ul>
</section>
