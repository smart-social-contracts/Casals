<script lang="ts">
  import { treemapLayout, colorAt, type TreemapInput, type TreemapRect } from '$lib/charts';

  interface Props {
    root: TreemapInput;
    format?: (v: number) => string;
    height?: number;
  }

  let { root, format = (v) => String(v), height = 360 }: Props = $props();

  const W = 760;

  const rects = $derived(treemapLayout(root, W, height));

  // Stable color per top-level section.
  const sectionColor = $derived.by(() => {
    const map = new Map<string, string>();
    let i = 0;
    for (const r of rects) {
      if (r.depth === 1 && !map.has(r.name)) map.set(r.name, colorAt(i++));
    }
    return map;
  });

  function colorFor(r: TreemapRect): string {
    return sectionColor.get(r.data.section ?? r.name) ?? '#94a3b8';
  }

  const total = $derived(rects.filter((r) => r.depth === 1).reduce((s, r) => s + r.value, 0));

  let hovered = $state<TreemapRect | null>(null);
</script>

<div class="w-full">
  {#if !rects.length || total <= 0}
    <div class="flex items-center justify-center text-sm text-primary-400" style="height:{height}px">
      Nothing to show for this window yet.
    </div>
  {:else}
    <svg viewBox="0 0 {W} {height}" class="w-full" style="height:auto" role="img" aria-label="Cycles treemap">
      <!-- canister tiles (leaves) -->
      {#each rects.filter((r) => r.depth === 3) as r (r.data.canister_id ?? r.name)}
        <g
          role="presentation"
          onmouseenter={() => (hovered = r)}
          onmouseleave={() => (hovered = null)}
        >
          <rect
            x={r.x} y={r.y} width={Math.max(0, r.w)} height={Math.max(0, r.h)}
            fill={colorFor(r)} fill-opacity={hovered === r ? 0.95 : 0.78}
            stroke="white" stroke-width="1" rx="2"
          />
          {#if r.w > 54 && r.h > 26}
            <text x={r.x + 5} y={r.y + 14} class="fill-white" font-size="10" font-weight="600">{r.name}</text>
            <text x={r.x + 5} y={r.y + 26} class="fill-white/85" font-size="9">{format(r.value)}</text>
          {/if}
        </g>
      {/each}

      <!-- section frames + labels -->
      {#each rects.filter((r) => r.depth === 1) as r (r.name)}
        <rect
          x={r.x} y={r.y} width={Math.max(0, r.w)} height={Math.max(0, r.h)}
          fill="none" stroke={sectionColor.get(r.name) ?? '#475569'} stroke-width="1.5" rx="3"
        />
        {#if r.w > 60 && r.h > 18}
          <text x={r.x + 5} y={r.y + 11} font-size="10" font-weight="700" style="fill:{sectionColor.get(r.name) ?? '#475569'}">{r.name}</text>
        {/if}
      {/each}

      <!-- stand frames -->
      {#each rects.filter((r) => r.depth === 2) as r, i (r.data.section + '/' + r.name + i)}
        <rect
          x={r.x} y={r.y} width={Math.max(0, r.w)} height={Math.max(0, r.h)}
          fill="none" stroke="white" stroke-opacity="0.6" stroke-dasharray="2 2" stroke-width="1" rx="2"
        />
      {/each}
    </svg>

    <div class="h-6 mt-1 px-1 text-xs text-primary-500">
      {#if hovered}
        <span class="font-medium text-primary-700">{hovered.data.section} / {hovered.data.stand} / {hovered.name}</span>
        · <span class="font-mono">{format(hovered.value)}</span>
        {#if hovered.data.canister_id}· <span class="font-mono text-primary-400">{hovered.data.canister_id}</span>{/if}
      {:else}
        <span class="text-primary-400">Hover a tile for section / stand / canister detail.</span>
      {/if}
    </div>
  {/if}
</div>
