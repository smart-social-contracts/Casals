<script lang="ts">
  import { page } from '$app/stores';
  import ArrangementEditor from '$lib/components/ArrangementEditor.svelte';
  import { getSectionArrangement, setSectionArrangement } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  const sectionName = $derived(decodeURIComponent($page.params.section ?? ''));

  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');
  let text = $state('');
  let canEdit = $state(false);

  async function load(name: string) {
    loading = true;
    error = '';
    try {
      const doc = await getSectionArrangement(name);
      canEdit = doc.can_edit !== false;
      text = doc.arrangements ? JSON.stringify(doc.arrangements, null, 2) : '';
    } catch (e: any) {
      canEdit = false;
      text = '';
      error = e?.message ?? 'Failed to load the arrangement';
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    const name = sectionName;
    if (!name || !$isAuthenticated) {
      loading = false;
      return;
    }
    void load(name);
  });

  async function save() {
    let arrangements: Record<string, unknown> | null;
    const raw = text.trim();
    if (!raw) {
      arrangements = null;
    } else {
      try {
        arrangements = JSON.parse(raw);
      } catch {
        toasts.error('Arrangement must be JSON');
        return;
      }
      if (arrangements === null || typeof arrangements !== 'object' || Array.isArray(arrangements)) {
        toasts.error('Arrangement must be an object with stand_template');
        return;
      }
    }
    saving = true;
    try {
      const saved = await setSectionArrangement({ section: sectionName, arrangements });
      text = saved.arrangements ? JSON.stringify(saved.arrangements, null, 2) : '';
      toasts.success(saved.arrangements ? 'Arrangement saved' : 'Arrangement removed');
    } catch (e: any) {
      toasts.error(e?.message ?? 'Save failed');
    } finally {
      saving = false;
    }
  }
</script>

<div class="flex flex-col gap-4 w-full h-[calc(100vh-11rem)] min-h-[32rem]">
  <div class="flex items-end justify-between gap-4 shrink-0">
    <div class="min-w-0">
      <a href="/" class="text-xs text-primary-500 hover:text-primary-800">Orchestra</a>
      <h1 class="text-lg font-semibold text-primary-900 mt-1">{sectionName}</h1>
      <p class="text-sm text-primary-500 mt-1">
        Arrangement for the next stand minted in this section. Stands that already exist stay as they are.
      </p>
    </div>
    {#if canEdit && !loading && !error && $isAuthenticated}
      <button class="btn-primary btn-sm shrink-0" type="button" disabled={saving} onclick={() => void save()}>
        {saving ? 'Saving…' : 'Save'}
      </button>
    {/if}
  </div>

  {#if !$isAuthenticated}
    <p class="text-sm text-primary-500">Log in with a commander grant that includes arrangement edit.</p>
  {:else if loading}
    <div class="skeleton flex-1 w-full"></div>
  {:else if error}
    <p class="text-sm text-red-600">{error}</p>
  {:else}
    <div class="relative flex-1 min-h-[70vh] w-full rounded-lg border border-[var(--color-border-primary)] bg-white overflow-hidden">
      <ArrangementEditor bind:text readOnly={!canEdit || saving} />
    </div>
    <p class="text-xs text-primary-400 shrink-0">An empty editor removes the arrangement. The object must contain <span class="font-mono">stand_template</span>.</p>
  {/if}
</div>
