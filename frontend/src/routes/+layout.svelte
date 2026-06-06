<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { initAuth, login, logout, isAuthenticated, principal } from '$lib/auth';
  import Toast from '$lib/components/Toast.svelte';

  let { children } = $props();

  const navLinks = [
    { href: '/', label: 'Orchestra' },
    { href: '/sheet', label: 'Sheet' },
    { href: '/cycles', label: 'Cycles' },
    { href: '/activity', label: 'Activity' },
    { href: '/commanders', label: 'Commanders' },
    { href: '/wasms', label: 'Authorized WASMs' },
    { href: '/settings', label: 'Settings' },
  ];

  let currentPath = $derived($page.url.pathname);

  onMount(() => {
    initAuth();
  });
</script>

<div class="min-h-screen flex flex-col bg-[var(--color-bg-secondary)]">
  <!-- Navbar -->
  <nav class="sticky top-0 z-30 bg-white border-b border-[var(--color-border-primary)]" style="box-shadow: var(--shadow-sm);">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
      <div class="flex items-center gap-6 min-w-0">
        <a href="/" class="flex items-center gap-2.5 group shrink-0">
          <img
            src="/logo.png"
            alt="Casals"
            class="w-8 h-8 object-contain"
            width="32"
            height="32"
          />
          <span class="text-lg font-semibold text-primary-900 group-hover:text-primary-700 transition-colors">
            Casals
          </span>
        </a>

        <div class="hidden sm:flex items-center gap-1">
          {#each navLinks as link (link.href)}
            <a
              href={link.href}
              class="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors {currentPath === link.href
                ? 'bg-primary-100 text-primary-900'
                : 'text-primary-500 hover:text-primary-800 hover:bg-primary-50'}"
            >
              {link.label}
            </a>
          {/each}
        </div>
      </div>

      <div class="flex items-center gap-3 shrink-0">
        {#if $isAuthenticated}
          <span
            class="hidden sm:block text-xs text-primary-400 font-mono truncate max-w-[160px]"
            title={$principal}
          >
            {$principal.slice(0, 5)}…{$principal.slice(-5)}
          </span>
          <button class="btn-ghost btn-sm" onclick={logout}>
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
            </svg>
            <span class="hidden sm:inline">Log out</span>
          </button>
        {:else}
          <button class="btn-primary btn-sm" onclick={login}>
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0" />
            </svg>
            <span class="hidden sm:inline">Login with Internet Identity</span>
            <span class="sm:hidden">Login</span>
          </button>
        {/if}
      </div>
    </div>

    <!-- Mobile nav -->
    <div class="sm:hidden border-t border-[var(--color-border-primary)] px-4 py-2 flex items-center gap-1 overflow-x-auto">
      {#each navLinks as link (link.href)}
        <a
          href={link.href}
          class="px-3 py-1.5 rounded-lg text-sm font-medium whitespace-nowrap transition-colors {currentPath === link.href
            ? 'bg-primary-100 text-primary-900'
            : 'text-primary-500 hover:text-primary-800 hover:bg-primary-50'}"
        >
          {link.label}
        </a>
      {/each}
    </div>
  </nav>

  <!-- Main content -->
  <main class="flex-1 w-full max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
    {@render children?.()}
  </main>

  <!-- Footer -->
  <footer class="border-t border-[var(--color-border-primary)]">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 py-4">
      <p class="text-xs text-primary-400 text-center">
        Casals &middot; Canister lifecycle orchestrator on the Internet Computer
      </p>
    </div>
  </footer>
</div>

<Toast />
