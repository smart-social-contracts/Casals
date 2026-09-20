<script lang="ts">
  // /wasms moved to /files (WASMs and asset bundles on one page). Old links
  // and bookmarks land here; the static build has no server-side redirects.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto('/files', { replaceState: true }));
</script>

<p class="text-sm text-primary-500">Moved to <a class="underline" href="/files">/files</a>…</p>
