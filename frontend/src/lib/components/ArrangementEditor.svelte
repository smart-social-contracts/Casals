<script lang="ts">
  import { onMount } from 'svelte';
  import type * as Monaco from 'monaco-editor';

  let { text = $bindable(''), readOnly = false } = $props();

  let host = $state<HTMLDivElement | null>(null);
  // Monaco's editor object must stay a plain instance. A counter wakes the
  // effects when it exists; the loaded arrangement often arrives after the
  // chunk, and reading `text` only once the editor is up is what paints it.
  let editor: Monaco.editor.IStandaloneCodeEditor | null = null;
  let ready = $state(0);
  let applying = false;
  let loadError = $state('');

  onMount(() => {
    let disposed = false;
    let current: Monaco.editor.IStandaloneCodeEditor | null = null;
    void import('$lib/arrangementEditor')
      .then(async ({ createArrangementEditor }) => {
        if (disposed || !host) return;
        current = await createArrangementEditor(host, text, readOnly);
        if (disposed) {
          current.dispose();
          return;
        }
        editor = current;
        ready += 1;
        const relayout = () => {
          if (!disposed) current?.layout();
        };
        requestAnimationFrame(relayout);
        current.onDidChangeModelContent(() => {
          if (!current || applying) return;
          const next = current.getValue();
          if (next !== text) text = next;
        });
      })
      .catch((e: unknown) => {
        loadError = e instanceof Error ? e.message : 'The code editor failed to load';
      });
    return () => {
      disposed = true;
      current?.dispose();
      editor = null;
    };
  });

  $effect(() => {
    if (!ready || !editor) return;
    const next = text;
    if (editor.getValue() === next) return;
    applying = true;
    editor.setValue(next);
    applying = false;
  });

  $effect(() => {
    if (!ready || !editor) return;
    editor.updateOptions({ readOnly });
  });
</script>

<div class="relative h-full w-full min-h-0">
  {#if loadError}
    <p class="absolute inset-x-0 top-0 z-10 px-3 py-2 text-sm text-red-700 bg-red-50 border-b border-red-200">{loadError}</p>
  {/if}
  <div class="absolute inset-0" bind:this={host}></div>
</div>
