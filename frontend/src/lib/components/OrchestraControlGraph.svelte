<script lang="ts">
  import type { Tree, OrchestrationStatus } from '$lib/api';
  import { shortPrincipal, canisterLink } from '$lib/api';
  import {
    mergeBatonStatus,
    findBatonsInTree,
    resolveBatons,
    governanceConsolePath,
    isBatonCanister,
    isMultisigCanister,
    isCasalsCanister,
  } from '$lib/orchestraGovernance';
  import {
    buildControlGraph,
    layoutControlGraph,
    graphDimensions,
    CONTROL_EDGE_META,
    type ControlGraphLayers,
    type ControlEdge,
    type ControlNode,
    DEFAULT_CONTROL_GRAPH_LAYERS,
  } from '$lib/orchestraControlGraph';

  interface Props {
    tree: Tree;
    orchestrationStatus?: OrchestrationStatus | null;
    casalsBackendId?: string;
    principalLabel?: (principal: string) => string;
  }

  let {
    tree,
    orchestrationStatus = null,
    casalsBackendId = '',
    principalLabel,
  }: Props = $props();

  let layers = $state<ControlGraphLayers>({ ...DEFAULT_CONTROL_GRAPH_LAYERS });
  let hoveredEdge = $state<ControlEdge | null>(null);
  let hoveredNode = $state<ControlNode | null>(null);
  let containerWidth = $state(960);

  const batons = $derived(
    mergeBatonStatus(findBatonsInTree(tree), resolveBatons(orchestrationStatus, tree)),
  );

  const graph = $derived(
    buildControlGraph(tree, orchestrationStatus, batons, {
      layers,
      casalsBackendId,
      principalLabel,
    }),
  );

  const layoutWidth = $derived(Math.max(containerWidth, 720));
  const positions = $derived(layoutControlGraph(graph, layoutWidth));
  const dims = $derived(graphDimensions(graph, layoutWidth));

  function nodeStyle(node: ControlNode): { fill: string; stroke: string } {
    const c = node.canister;
    if (!c) return { fill: '#f8fafc', stroke: '#cbd5e1' };
    if (isCasalsCanister(c)) return { fill: '#eef2ff', stroke: '#a5b4fc' };
    if (isMultisigCanister(c)) return { fill: '#ecfdf5', stroke: '#6ee7b7' };
    if (isBatonCanister(c)) return { fill: '#fff7ed', stroke: '#fdba74' };
    if (c.kind === 'frontend') return { fill: '#eff6ff', stroke: '#bfdbfe' };
    return { fill: '#f5f3ff', stroke: '#ddd6fe' };
  }

  function nodeHref(node: ControlNode): string | null {
    if (node.canister) {
      return governanceConsolePath(node.canister) ?? canisterLink(node.canister);
    }
    return null;
  }

  function edgePath(from: { x: number; y: number }, to: { x: number; y: number }): string {
    const dy = to.y - from.y;
    const curve = Math.max(24, Math.abs(dy) * 0.35);
    const c1y = from.y + curve;
    const c2y = to.y - curve;
    return `M ${from.x} ${from.y + 28} C ${from.x} ${c1y}, ${to.x} ${c2y}, ${to.x} ${to.y - 28}`;
  }

  function toggleLayer(key: keyof ControlGraphLayers): void {
    layers = { ...layers, [key]: !layers[key] };
  }
</script>

<svelte:window bind:innerWidth={containerWidth} />

<div class="w-full">
  {#if graph.nodes.length === 0}
    <div class="flex items-center justify-center text-sm text-primary-400 py-16">
      No control relationships to graph — enable a layer or refresh orchestration status.
    </div>
  {:else}
    <div class="mb-4 rounded-lg border border-[var(--color-border-primary)] bg-primary-50/60 px-3 py-2.5 space-y-2">
      <div class="text-[10px] font-semibold uppercase tracking-wider text-primary-500">Edge layers</div>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                 {layers.icControllers ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-primary-600 border-primary-200'}"
          onclick={() => toggleLayer('icControllers')}
        >
          IC controllers
        </button>
        <button
          type="button"
          class="px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                 {layers.commanders ? 'bg-slate-600 text-white border-slate-600' : 'bg-white text-primary-600 border-primary-200'}"
          onclick={() => toggleLayer('commanders')}
        >
          Commanders
        </button>
        <button
          type="button"
          class="px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                 {layers.baton ? 'bg-orange-600 text-white border-orange-600' : 'bg-white text-primary-600 border-primary-200'}"
          onclick={() => toggleLayer('baton')}
        >
          Baton
        </button>
      </div>
      <div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-primary-600">
        {#each Object.entries(CONTROL_EDGE_META) as [type, meta] (type)}
          <span class="inline-flex items-center gap-1.5" title={meta.tooltip}>
            <svg width="28" height="8" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="28"
                y2="4"
                stroke={meta.stroke}
                stroke-width={meta.width ?? 1.5}
                stroke-dasharray={meta.dash ?? undefined}
              />
            </svg>
            {meta.label}
          </span>
        {/each}
      </div>
    </div>

    {#if graph.warnings.length}
      <div class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 space-y-1">
        {#each graph.warnings as warning (warning)}
          <div>{warning}</div>
        {/each}
      </div>
    {/if}

    <div class="overflow-auto rounded-lg border border-[var(--color-border-primary)] bg-white">
      <svg
        width={dims.width}
        height={dims.height}
        viewBox="0 0 {dims.width} {dims.height}"
        class="min-w-full"
        role="img"
        aria-label="Orchestra control graph"
      >
        <defs>
          {#each Object.entries(CONTROL_EDGE_META) as [type, meta] (type)}
            <marker
              id="arrow-{type}"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 Z" fill={meta.stroke} />
            </marker>
          {/each}
        </defs>

        {#each graph.edges as edge (edge.id)}
          {@const from = positions.get(edge.from)}
          {@const to = positions.get(edge.to)}
          {@const meta = CONTROL_EDGE_META[edge.type]}
          {#if from && to}
            <path
              d={edgePath(from, to)}
              fill="none"
              stroke={meta.stroke}
              stroke-width={hoveredEdge?.id === edge.id ? (meta.width ?? 1.5) + 1 : (meta.width ?? 1.5)}
              stroke-dasharray={meta.dash}
              marker-end="url(#arrow-{edge.type})"
              opacity={hoveredEdge && hoveredEdge.id !== edge.id ? 0.25 : 0.85}
              class="transition-opacity"
              onmouseenter={() => (hoveredEdge = edge)}
              onmouseleave={() => (hoveredEdge = null)}
            >
              <title>{meta.tooltip}{edge.label ? ` (${edge.label})` : ''}</title>
            </path>
          {/if}
        {/each}

        {#each graph.nodes as node (node.id)}
          {@const pos = positions.get(node.id)}
          {#if pos}
            {@const href = nodeHref(node)}
            {@const w = 148}
            {@const h = 56}
            {@const style = nodeStyle(node)}
            <g
              transform="translate({pos.x - w / 2}, {pos.y - h / 2})"
              onmouseenter={() => (hoveredNode = node)}
              onmouseleave={() => (hoveredNode = null)}
            >
              {#if href}
                <a href={href} target={href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">
                  <rect
                    width={w}
                    height={h}
                    rx="8"
                    fill={style.fill}
                    stroke={style.stroke}
                    stroke-width="2"
                    opacity={hoveredEdge && !graph.edges.some((e) => (e.from === node.id || e.to === node.id) && e.id === hoveredEdge.id) ? 0.55 : 1}
                  />
                </a>
              {:else}
                <rect
                  width={w}
                  height={h}
                  rx="8"
                  fill={style.fill}
                  stroke={style.stroke}
                  stroke-width="2"
                />
              {/if}
              <text x="10" y="22" fill="#1e293b" class="text-[11px] font-semibold pointer-events-none">
                {node.label.length > 18 ? `${node.label.slice(0, 16)}…` : node.label}
              </text>
              {#if node.sublabel}
                <text x="10" y="40" fill="#64748b" class="text-[9px] uppercase tracking-wide pointer-events-none">
                  {node.sublabel}
                </text>
              {/if}
            </g>
          {/if}
        {/each}
      </svg>
    </div>

    <div class="mt-3 pt-2 border-t border-[var(--color-border-primary)] text-xs text-primary-500 min-h-[1.25rem]">
      {#if hoveredEdge}
        <span class="text-primary-700">{CONTROL_EDGE_META[hoveredEdge.type].tooltip}</span>
      {:else if hoveredNode}
        <span class="font-mono text-[11px] text-primary-700">
          {hoveredNode.label}
          {#if hoveredNode.canister?.canister_id}
            · {hoveredNode.canister.canister_id}
          {:else if hoveredNode.principal}
            · {shortPrincipal(hoveredNode.principal)}
          {/if}
          {#if hoveredNode.section && hoveredNode.stand}
            · {hoveredNode.section} / {hoveredNode.stand}
          {/if}
        </span>
      {:else}
        <span>Hover an edge or node for authority details. Scroll to pan wide graphs.</span>
      {/if}
    </div>
  {/if}
</div>
