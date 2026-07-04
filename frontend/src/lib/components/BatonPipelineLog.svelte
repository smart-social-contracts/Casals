<script lang="ts">
  import type { PipelineLogLine } from '$lib/batonPipelineLog';
  import { formatPipelineTimestamp, pipelineLinesToText } from '$lib/batonPipelineLog';
  import { copyText } from '$lib/clipboard';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    lines?: PipelineLogLine[];
    status?: string;
    error?: string;
    busy?: boolean;
    title?: string;
    maxHeight?: string;
    showCopy?: boolean;
  }

  let {
    lines = [],
    status = '',
    error = '',
    busy = false,
    title = 'Pipeline log',
    maxHeight = '14rem',
    showCopy = true,
  }: Props = $props();

  let bodyEl = $state<HTMLDivElement | null>(null);

  async function copyLog() {
    const text = pipelineLinesToText(lines);
    if (error) {
      const full = text ? `${text}\n${formatPipelineTimestamp(Date.now())}  ERROR       ${error}` : error;
      if (await copyText(full)) toasts.success('Log copied');
      return;
    }
    if (!text) {
      toasts.error('Nothing to copy');
      return;
    }
    if (await copyText(text)) toasts.success('Log copied');
  }

  $effect(() => {
    lines.length;
    status;
    error;
    if (bodyEl) bodyEl.scrollTop = bodyEl.scrollHeight;
  });
</script>

<div class="pipeline-terminal" aria-live="polite" aria-busy={busy}>
  <div class="pipeline-terminal-header">
    <span class="pipeline-dot" class:pulse={busy}></span>
    <span class="pipeline-title">{title}</span>
    {#if status}
      <span class="pipeline-status">{status}</span>
    {:else if busy}
      <span class="pipeline-status">running…</span>
    {/if}
    {#if showCopy && (lines.length > 0 || error)}
      <button
        type="button"
        class="pipeline-copy"
        title="Copy log"
        aria-label="Copy log"
        onclick={() => copyLog()}
      >
        ⧉
      </button>
    {/if}
  </div>
  <div class="pipeline-terminal-body" bind:this={bodyEl} style:max-height={maxHeight}>
    {#if lines.length === 0}
      <div class="pipeline-line dim">
        <span class="pipeline-ts">—</span>
        <span class="pipeline-label info">…</span>
        <span class="pipeline-msg">{busy ? 'Waiting for pipeline output…' : 'No log entries yet.'}</span>
      </div>
    {:else}
      {#each lines as line (line.id)}
        <div class="pipeline-line">
          <span class="pipeline-ts">{formatPipelineTimestamp(line.ts)}</span>
          <span class="pipeline-label {line.level}">{line.label}</span>
          <span class="pipeline-msg">{line.message}</span>
        </div>
      {/each}
    {/if}
    {#if error}
      <div class="pipeline-line">
        <span class="pipeline-ts">{formatPipelineTimestamp(Date.now())}</span>
        <span class="pipeline-label error">ERROR</span>
        <span class="pipeline-msg">{error}</span>
      </div>
    {/if}
  </div>
</div>

<style>
  .pipeline-terminal {
    border-radius: 0.5rem;
    overflow: hidden;
    border: 1px solid #1f2937;
    background: #0a0a0a;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.6875rem;
    line-height: 1.45;
  }
  .pipeline-terminal-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.625rem;
    background: #111827;
    border-bottom: 1px solid #1f2937;
    color: #9ca3af;
  }
  .pipeline-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 9999px;
    background: #4b5563;
    flex-shrink: 0;
  }
  .pipeline-dot.pulse {
    background: #22c55e;
    box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.5);
    animation: pulse 1.5s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.45; }
  }
  .pipeline-title {
    color: #d1d5db;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 0.625rem;
  }
  .pipeline-status {
    margin-left: auto;
    color: #fbbf24;
    font-weight: 500;
  }
  .pipeline-copy {
    margin-left: 0.25rem;
    padding: 0.125rem 0.375rem;
    border: 1px solid #374151;
    border-radius: 0.25rem;
    background: #1f2937;
    color: #d1d5db;
    font-size: 0.75rem;
    line-height: 1;
    cursor: pointer;
  }
  .pipeline-copy:hover {
    background: #374151;
    color: #fff;
  }
  .pipeline-terminal-body {
    padding: 0.5rem 0.625rem;
    overflow: auto;
    color: #e5e7eb;
  }
  .pipeline-line {
    display: grid;
    grid-template-columns: 5.5rem 6.5rem 1fr;
    gap: 0.5rem;
    padding: 0.125rem 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .pipeline-line.dim {
    color: #6b7280;
  }
  .pipeline-ts {
    color: #6b7280;
    flex-shrink: 0;
  }
  .pipeline-label {
    font-weight: 600;
    flex-shrink: 0;
  }
  .pipeline-label.info { color: #93c5fd; }
  .pipeline-label.ok { color: #4ade80; }
  .pipeline-label.warn { color: #fbbf24; }
  .pipeline-label.error { color: #f87171; }
  .pipeline-msg {
    color: #e5e7eb;
  }
</style>
