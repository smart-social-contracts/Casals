<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import { page } from '$app/stores';
  import { initAuth, login, logout, isAuthenticated, principal, accessDenied, dismissAccessDenied } from '$lib/auth';
  import { backendCanisterId, casalsMetadata, initLocalNetworkHints } from '$lib/api';
  import {
    pendingGovernanceCount,
    startGovernancePolling,
    stopGovernancePolling,
  } from '$lib/stores/governancePending';
  import Toast from '$lib/components/Toast.svelte';
  import AccessDeniedModal from '$lib/components/AccessDeniedModal.svelte';

  let { children } = $props();

  const navLinks = [
    { href: '/', label: 'Orchestra' },
    { href: '/wasms', label: 'WASMs' },
    { href: '/commanders', label: 'Commanders' },
    { href: '/aliases', label: 'Aliases' },
    { href: '/orchestration', label: 'Orchestration' },
    { href: '/multisig', label: 'Multisig' },
    { href: '/cycles', label: 'Cycles' },
    { href: '/activity', label: 'Activity' },
    { href: '/sheet', label: 'Sheet' },
    { href: '/arrangements', label: 'Arrangements' },
    { href: '/settings', label: 'Settings' },
  ];

  let sidebarOpen = $state(false);
  let isDesktop = $state(false);
  let orchestraName = $state('');
  let currentPath = $derived($page.url.pathname);
  let mobileSidebarOpen = $derived(sidebarOpen && !isDesktop);

  function toggleSidebar() {
    sidebarOpen = !sidebarOpen;
  }

  function closeSidebar() {
    sidebarOpen = false;
  }

  async function handleLogin() {
    await login(backendCanisterId());
  }

  $effect(() => {
    if (!mobileSidebarOpen) return;
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeSidebar();
    };
    window.addEventListener('keydown', onKeydown);
    return () => window.removeEventListener('keydown', onKeydown);
  });

  onMount(() => {
    void initAuth(backendCanisterId());
    void initLocalNetworkHints();
    void casalsMetadata()
      .then((md) => {
        orchestraName = (md.orchestra_name ?? '').trim();
      })
      .catch(() => {});

    const mq = window.matchMedia('(min-width: 1024px)');
    const syncViewport = () => {
      isDesktop = mq.matches;
      if (mq.matches) closeSidebar();
    };
    syncViewport();
    mq.addEventListener('change', syncViewport);
    return () => mq.removeEventListener('change', syncViewport);
  });

  $effect(() => {
    if ($isAuthenticated) {
      startGovernancePolling();
    } else {
      stopGovernancePolling();
    }
    return () => stopGovernancePolling();
  });

  function pendingBadge(n: number): string {
    return n > 9 ? '9+' : String(n);
  }
</script>

<svelte:head>
  <title>Casals{orchestraName ? ` · ${orchestraName}` : ''}</title>
</svelte:head>

{#snippet sidebarNav()}
  <nav class="flex-1 overflow-y-auto px-3 py-4">
    <ul class="space-y-1">
      {#each navLinks as link (link.href)}
        <li>
          <a
            href={link.href}
            onclick={closeSidebar}
            class="flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors {currentPath === link.href
              ? 'bg-primary-100 text-primary-900'
              : 'text-primary-500 hover:text-primary-800 hover:bg-primary-50'}"
          >
            <span>{link.label}</span>
            {#if link.href === '/commanders' && $pendingGovernanceCount > 0}
              <span
                class="min-w-[1.25rem] h-5 px-1.5 rounded-full bg-red-600 text-white text-[11px] font-bold leading-none inline-flex items-center justify-center"
                aria-hidden="true"
              >
                {pendingBadge($pendingGovernanceCount)}
              </span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  </nav>
{/snippet}

{#snippet sidebarFooter()}
  {#if $isAuthenticated}
    <div class="px-4 py-3 border-t border-[var(--color-border-primary)] shrink-0">
      <p class="text-xs text-primary-400 font-mono truncate" title={$principal}>
        {$principal.slice(0, 5)}…{$principal.slice(-5)}
      </p>
    </div>
  {/if}
{/snippet}

<div class="min-h-screen flex flex-col bg-[var(--color-bg-secondary)]" class:overflow-hidden={mobileSidebarOpen}>
  <!-- Header -->
  <header class="sticky top-0 z-30 bg-white border-b border-[var(--color-border-primary)]" style="box-shadow: var(--shadow-sm);">
    <div class="w-full px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
      <div class="flex items-center gap-3 min-w-0">
        <button
          type="button"
          class="btn-ghost btn-sm p-2 relative lg:hidden"
          onclick={toggleSidebar}
          aria-expanded={sidebarOpen}
          aria-controls="app-sidebar"
          aria-label={sidebarOpen ? 'Close menu' : 'Open menu'}
        >
          {#if $pendingGovernanceCount > 0}
            <span
              class="absolute -top-0.5 -right-0.5 min-w-[1.125rem] h-[1.125rem] px-1 rounded-full bg-red-600 text-white text-[10px] font-bold leading-none flex items-center justify-center pointer-events-none"
              aria-label="{$pendingGovernanceCount} pending orchestration approvals"
            >
              {pendingBadge($pendingGovernanceCount)}
            </span>
          {/if}
          {#if sidebarOpen}
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          {:else}
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
            </svg>
          {/if}
        </button>

        <a href="/" class="flex items-center gap-2.5 group shrink-0">
          <img
            src="/logo.png"
            alt="Casals"
            class="w-8 h-8 object-contain"
            width="32"
            height="32"
          />
          <span class="text-lg font-semibold text-primary-900 group-hover:text-primary-700 transition-colors truncate">
            Casals{#if orchestraName}<span class="text-primary-500 font-medium"> · {orchestraName}</span>{/if}
          </span>
        </a>
      </div>

      <div class="flex items-center gap-3 shrink-0">
        {#if $isAuthenticated}
          <span
            class="hidden sm:block text-xs text-primary-400 font-mono truncate max-w-[160px]"
            title={$principal}
          >
            {$principal.slice(0, 5)}…{$principal.slice(-5)}
          </span>
          <button class="btn-ghost btn-sm" onclick={() => logout()}>
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
            </svg>
            <span class="hidden sm:inline">Log out</span>
          </button>
        {:else}
          <button class="btn-primary btn-sm" onclick={handleLogin}>
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0" />
            </svg>
            <span class="hidden sm:inline">Login with Internet Identity</span>
            <span class="sm:hidden">Login</span>
          </button>
        {/if}
      </div>
    </div>
  </header>

  <div class="flex flex-1 min-w-0">
    <aside
      id="app-sidebar"
      class="app-sidebar-desktop hidden lg:flex w-64 shrink-0 flex-col bg-white border-r border-[var(--color-border-primary)]"
      aria-label="Main navigation"
    >
      {@render sidebarNav()}
      {@render sidebarFooter()}
    </aside>

    {#if mobileSidebarOpen}
      <button
        type="button"
        class="fixed inset-0 z-40 cursor-default lg:hidden"
        style="background: var(--color-bg-overlay);"
        aria-label="Close menu"
        onclick={closeSidebar}
        transition:fade={{ duration: 200 }}
      ></button>

      <aside
        class="fixed top-0 left-0 z-50 h-full w-64 bg-white border-r border-[var(--color-border-primary)] flex flex-col lg:hidden"
        style="box-shadow: var(--shadow-xl);"
        transition:fly={{ x: -256, duration: 200 }}
      >
        <div class="h-14 flex items-center gap-2.5 px-4 border-b border-[var(--color-border-primary)] shrink-0">
          <img src="/logo.png" alt="" class="w-7 h-7 object-contain" width="28" height="28" />
          <span class="text-base font-semibold text-primary-900 truncate">
            Casals{#if orchestraName}<span class="text-primary-500 font-medium"> · {orchestraName}</span>{/if}
          </span>
        </div>

        {@render sidebarNav()}
        {@render sidebarFooter()}
      </aside>
    {/if}

    <div class="flex-1 flex flex-col min-w-0">
      <main class="flex-1 w-full px-4 sm:px-6 py-6 sm:py-8">
        {@render children?.()}
      </main>

      <footer class="border-t border-[var(--color-border-primary)]">
        <div class="w-full px-4 sm:px-6 py-4">
          <p class="text-xs text-primary-400 text-center">
            Casals &middot; Canister lifecycle orchestrator on the Internet Computer
          </p>
        </div>
      </footer>
    </div>
  </div>
</div>

<Toast />

{#if $accessDenied}
  <AccessDeniedModal
    message={$accessDenied.message}
    principal={$accessDenied.principal}
    onclose={dismissAccessDenied}
  />
{/if}
