<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getTree,
    getStatus,
    createSection,
    createDesk,
    registerStand,
    setCommander,
    createStand,
    upgradeTo,
    createSnapshot,
    revertSnapshot,
    stopCanister,
    startCanister,
    setLogVisibility,
    getEvents,
    getCanisterLogs,
    listAuthorizedWasms,
    standBrowse,
    standExec,
    shortHash,
    shortPrincipal,
    standLink,
  } from '$lib/api';
  import type {
    Tree, Status, Section, Desk, Stand, UpdateResult,
    OrchestrationEvent, CanisterLogRecord, AuthorizedWasm,
  } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import FormModal from '$lib/components/FormModal.svelte';
  import type { Field } from '$lib/components/FormModal.svelte';

  type Values = Record<string, string | boolean>;

  interface ModalConfig {
    title: string;
    description?: string;
    fields: Field[];
    submitLabel: string;
    onsubmit: (values: Values) => Promise<UpdateResult>;
  }

  let tree = $state<Tree | null>(null);
  let status = $state<Status | null>(null);
  let loading = $state(true);
  let error = $state('');
  let catalog = $state<AuthorizedWasm[]>([]);

  let expandedSections = $state<Record<string, boolean>>({});
  let expandedDesks = $state<Record<string, boolean>>({});

  // Per-stand expandable detail panel (status + recent events + canister logs).
  let expandedStands = $state<Record<string, boolean>>({});
  let standEvents = $state<Record<string, OrchestrationEvent[]>>({});
  let standLogs = $state<Record<string, CanisterLogRecord[]>>({});
  let standLogErr = $state<Record<string, string>>({});
  let standLoading = $state<Record<string, boolean>>({});

  // Basilisk introspection (only for stands built with __basilisk_features__).
  let browseData = $state<Record<string, unknown>>({});
  let browseErr = $state<Record<string, string>>({});
  let browseBusy = $state<Record<string, boolean>>({});
  let consoleCode = $state<Record<string, string>>({});
  let consoleOut = $state<Record<string, string>>({});
  let consoleErr = $state<Record<string, string>>({});
  let consoleBusy = $state<Record<string, boolean>>({});

  let modal = $state<ModalConfig | null>(null);
  let modalBusy = $state(false);

  async function load() {
    loading = true;
    error = '';
    try {
      [tree, status, catalog] = await Promise.all([
        getTree(),
        getStatus(),
        listAuthorizedWasms().catch(() => [] as AuthorizedWasm[]),
      ]);
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  // Build the WASM-version <select> options for a family. The first option is
  // the bare family name, which the backend resolves to the latest version; the
  // rest pin a specific version. Falls back to key-prefix matching when the
  // catalog entries don't include a `family` field (old backend).
  function versionOptions(family: string): { value: string; label: string }[] {
    if (!family || !catalog.length) return [];
    const members = catalog
      .filter((w) => (w.family || w.key.split('@')[0]) === family)
      .sort((a, b) => verCmp(b.version || b.key.split('@')[1] || '', a.version || a.key.split('@')[1] || ''));
    if (members.length === 0) return [];
    const latest = members.find((m) => m.latest) ?? members[0];
    const latestVer = latest.version || latest.key.split('@')[1] || '?';
    const opts = [{ value: family, label: `Latest (v${latestVer})` }];
    for (const m of members) {
      const ver = m.version || m.key.split('@')[1] || '?';
      opts.push({ value: m.key, label: `v${ver}${m.latest !== false && m === latest ? ' · latest' : ''}` });
    }
    return opts;
  }

  // All distinct families in the catalog (for free-text fallback datalist).
  const allFamilies = $derived(
    [...new Set(catalog.map((w) => w.family || w.key.split('@')[0]).filter(Boolean))].sort()
  );

  function verCmp(a: string, b: string): number {
    const pa = (a || '0').split('.').map((n) => parseInt(n, 10) || 0);
    const pb = (b || '0').split('.').map((n) => parseInt(n, 10) || 0);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const d = (pa[i] ?? 0) - (pb[i] ?? 0);
      if (d !== 0) return d;
    }
    return 0;
  }

  function familyOf(wasmKey: string): string {
    return (wasmKey || '').split('@')[0];
  }

  function shortId(id: string): string {
    if (!id) return '';
    return id.length > 13 ? `${id.slice(0, 5)}…${id.slice(-5)}` : id;
  }

  // Desired-placement label for a section/desk (target for new canisters).
  function placementLabel(o: { subnet?: string; subnet_type?: string }): string {
    if (o.subnet) return `subnet ${shortId(o.subnet)}`;
    if (o.subnet_type) return `subnet type: ${o.subnet_type}`;
    return '';
  }

  // Families present in a desk (across its stands), for desk-wide upgrades.
  function deskFamilies(desk: Desk): string[] {
    const fams = new Set<string>();
    for (const s of desk.stands) {
      const f = familyOf(s.wasm_key);
      if (f) fams.add(f);
    }
    return [...fams];
  }

  onMount(load);

  function sectionOpen(name: string): boolean {
    return expandedSections[name] !== false;
  }
  function deskOpen(key: string): boolean {
    return expandedDesks[key] !== false;
  }
  function toggleSection(name: string) {
    expandedSections[name] = !sectionOpen(name);
  }
  function toggleDesk(key: string) {
    expandedDesks[key] = !deskOpen(key);
  }

  async function toggleStand(stand: Stand) {
    const cid = stand.canister_id;
    if (!cid) return;
    const open = !expandedStands[cid];
    expandedStands[cid] = open;
    if (open) await loadStandDetails(cid);
  }

  async function loadStandDetails(cid: string) {
    standLoading[cid] = true;
    standLogErr[cid] = '';
    try {
      // The audit log lives in Casals; the runtime logs are fetched straight
      // from the management canister (only works if log_visibility is public).
      const [evs, logs] = await Promise.all([
        getEvents({ canister_id: cid, take: 8 }),
        getCanisterLogs(cid).catch((e: any) => {
          standLogErr[cid] = e?.message ?? String(e);
          return [] as CanisterLogRecord[];
        }),
      ]);
      standEvents[cid] = evs;
      standLogs[cid] = logs;
    } catch (e: any) {
      standLogErr[cid] = e?.message ?? String(e);
    } finally {
      standLoading[cid] = false;
    }
  }

  async function makeLogsPublic(stand: Stand) {
    try {
      await setLogVisibility({ stand: stand.name, public: true });
      toasts.success('Logs set to public');
      await loadStandDetails(stand.canister_id);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to set log visibility');
    }
  }

  async function runBrowse(stand: Stand, query?: Record<string, unknown>) {
    const cid = stand.canister_id;
    browseBusy[cid] = true;
    browseErr[cid] = '';
    try {
      const r = await standBrowse(stand.name, query);
      browseData[cid] = r.result;
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      browseErr[cid] = /__browse__|method|not found|no query method/i.test(msg)
        ? 'This stand does not expose introspection (rebuild with __basilisk_features__).'
        : msg;
    } finally {
      browseBusy[cid] = false;
    }
  }

  async function runExec(stand: Stand) {
    const cid = stand.canister_id;
    const code = consoleCode[cid] ?? '';
    if (!code.trim()) return;
    consoleBusy[cid] = true;
    consoleErr[cid] = '';
    try {
      const r = await standExec(stand.name, code);
      consoleOut[cid] = r.output ?? '';
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      consoleErr[cid] = /__shell__|method|not found/i.test(msg)
        ? 'This stand does not expose a Python shell (rebuild with __basilisk_features__).'
        : msg;
    } finally {
      consoleBusy[cid] = false;
    }
  }

  function fmtTs(nanos: bigint): string {
    try {
      return new Date(Number(nanos / 1_000_000n)).toLocaleTimeString();
    } catch {
      return '';
    }
  }

  function evBadge(btype: string): string {
    if (btype.includes('failed')) return 'bg-red-50 text-red-700 border border-red-200';
    if (btype === 'revert') return 'bg-amber-50 text-amber-700 border border-amber-200';
    return 'badge-neutral';
  }

  function evSummary(e: OrchestrationEvent): string {
    const p = e.payload ?? {};
    switch (e.btype) {
      case 'upgraded': return `→ ${p.wasm_key ?? ''}`;
      case 'upgrade_failed': return p.reason ?? '';
      case 'revert': return p.reason ? `rolled back: ${p.reason}` : `snapshot ${String(p.snapshot_id ?? '').slice(0, 8)}`;
      case 'revert_failed': return p.error ?? '';
      case 'snapshot': return `snapshot ${String(p.snapshot_id ?? '').slice(0, 8)}`;
      case 'stand_created': return `${p.name ?? ''} (${p.wasm_key ?? ''})`;
      case 'stand_reinstalled': return `${p.name ?? ''} (${p.wasm_key ?? ''})`;
      case 'assets_uploaded': return `${p.bytes ?? 0} bytes`;
      case 'cycles_topup': return `+${p.amount ?? ''}`;
      case 'create_failed': return 'module hash mismatch';
      default: { const k = Object.keys(p); return k.length ? JSON.stringify(p).slice(0, 80) : ''; }
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toasts.info('Copied to clipboard');
    } catch {
      toasts.error('Could not copy');
    }
  }

  // Strip empty optional string fields so we only send what the user provided.
  function clean(values: Values): Record<string, any> {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(values)) {
      if (typeof v === 'string') {
        const trimmed = v.trim();
        if (trimmed !== '') out[k] = trimmed;
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  function openModal(config: ModalConfig) {
    modal = config;
  }
  function closeModal() {
    if (modalBusy) return;
    modal = null;
  }

  async function submitModal(values: Values) {
    if (!modal) return;
    modalBusy = true;
    try {
      await modal.onsubmit(values);
      toasts.success(`${modal.title} succeeded`);
      modal = null;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Operation failed');
    } finally {
      modalBusy = false;
    }
  }

  // Direct (no extra fields) per-stand lifecycle action.
  async function runStandAction(label: string, fn: () => Promise<UpdateResult>) {
    try {
      await fn();
      toasts.success(`${label} succeeded`);
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Operation failed');
    }
  }

  // ── Modal openers ──

  function openCreateSection() {
    openModal({
      title: 'Create section',
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'Deployed realms' },
        { name: 'description', label: 'Description', type: 'textarea' },
        { name: 'commander_principal', label: 'Commander principal', placeholder: 'aaaaa-aa' },
      ],
      submitLabel: 'Create section',
      onsubmit: (v) => createSection(clean(v) as any),
    });
  }

  function openCreateDesk(section: Section) {
    openModal({
      title: 'Create desk',
      description: `In section "${section.name}"`,
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'Agora' },
        { name: 'description', label: 'Description', type: 'textarea' },
        { name: 'commander_principal', label: 'Commander principal', placeholder: 'aaaaa-aa' },
      ],
      submitLabel: 'Create desk',
      onsubmit: (v) => createDesk({ ...(clean(v) as any), section: section.name }),
    });
  }

  function openRegisterStand(desk: Desk) {
    openModal({
      title: 'Register stand',
      description: `In desk "${desk.name}" — registers an existing canister`,
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'realm_frontend' },
        { name: 'canister_id', label: 'Canister id', required: true, placeholder: 'aaaaa-aa' },
        {
          name: 'kind',
          label: 'Kind',
          type: 'select',
          value: 'backend',
          options: [
            { value: 'backend', label: 'Backend' },
            { value: 'frontend', label: 'Frontend' },
          ],
        },
      ],
      submitLabel: 'Register stand',
      onsubmit: (v) => registerStand({ ...(clean(v) as any), desk: desk.name }),
    });
  }

  function openSetCommander(target: { section?: string; desk?: string }, current: string) {
    const label = target.section ? `section "${target.section}"` : `desk "${target.desk}"`;
    openModal({
      title: 'Set commander',
      description: `Authorized commander for ${label}`,
      fields: [
        {
          name: 'commander_principal',
          label: 'Commander principal',
          required: true,
          value: current ?? '',
          placeholder: 'aaaaa-aa',
        },
      ],
      submitLabel: 'Set commander',
      onsubmit: (v) => setCommander({ ...target, commander_principal: String(v.commander_principal).trim() }),
    });
  }

  function openCreateStand(desk: Desk) {
    openModal({
      title: 'Create stand',
      description: `In desk "${desk.name}" — creates a canister + installs an authorized WASM`,
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'realm_backend' },
        {
          name: 'kind',
          label: 'Kind',
          type: 'select',
          value: 'backend',
          options: [
            { value: 'backend', label: 'Backend' },
            { value: 'frontend', label: 'Frontend' },
          ],
        },
        allFamilies.length > 0
          ? { name: 'wasm_key', label: 'WASM family', type: 'select', required: true,
              value: allFamilies[0], options: allFamilies.map((f) => ({ value: f, label: f })) }
          : { name: 'wasm_key', label: 'WASM family or key', required: true, placeholder: 'hello-world-basilisk' },
      ],
      submitLabel: 'Create stand',
      onsubmit: (v) => createStand({ ...(clean(v) as any), desk: desk.name }),
    });
  }

  function openUpgradeDesk(desk: Desk) {
    const fams = deskFamilies(desk);
    const opts = fams.length === 1 ? versionOptions(fams[0]) : [];
    // Family options list for the catalog-families select (fallback when no version info).
    const familyOpts = allFamilies.map((f) => ({ value: f, label: f }));
    openModal({
      title: 'Upgrade desk',
      description: `Upgrade all stands in desk "${desk.name}"`,
      fields: [
        opts.length > 0
          ? { name: 'wasm_key', label: 'Version', type: 'select', value: fams[0], options: opts }
          : familyOpts.length > 0
            ? { name: 'wasm_key', label: 'WASM family', type: 'select',
                value: fams[0] || familyOpts[0].value, options: familyOpts }
            : { name: 'wasm_key', label: 'WASM key', required: true, placeholder: 'hello-world-basilisk' },
      ],
      submitLabel: 'Upgrade',
      onsubmit: (v) => upgradeTo({ desk: desk.name, wasm_key: String(v.wasm_key).trim() }),
    });
  }

  function openUpgradeStand(stand: Stand) {
    const family = familyOf(stand.wasm_key);
    const opts = versionOptions(family);
    const familyOpts = allFamilies.map((f) => ({ value: f, label: f }));
    openModal({
      title: 'Upgrade stand',
      description: `Upgrade stand "${stand.name}"${family ? ` · family ${family}` : ''}`,
      fields: [
        opts.length > 0
          ? { name: 'wasm_key', label: 'Version', type: 'select', value: family, options: opts }
          : familyOpts.length > 0
            ? { name: 'wasm_key', label: 'WASM family', type: 'select',
                value: family || familyOpts[0].value, options: familyOpts }
            : { name: 'wasm_key', label: 'WASM key', required: true, value: stand.wasm_key ?? '',
                placeholder: 'hello-world-basilisk' },
      ],
      submitLabel: 'Upgrade',
      onsubmit: (v) => upgradeTo({ stand: stand.name, wasm_key: String(v.wasm_key).trim() }),
    });
  }
</script>

<svelte:head><title>Casals · Orchestra</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <!-- Header -->
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Orchestra</h1>
      <p class="text-sm text-primary-500 mt-1">Section → Desk → Stand lifecycle orchestration</p>
    </div>
    <div class="flex items-center gap-2 self-start">
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={openCreateSection}>
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          New section
        </button>
      {/if}
      <button class="btn-secondary btn-sm" onclick={load}>
        <svg class="w-4 h-4 {loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
        </svg>
        Refresh
      </button>
    </div>
  </div>

  <!-- Status strip -->
  {#if status}
    <div class="card px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
      <span class="text-xs font-semibold text-primary-400 uppercase tracking-wider">v{status.version}</span>
      <span class="text-primary-600"><strong class="text-primary-900">{status.sections}</strong> sections</span>
      <span class="text-primary-600"><strong class="text-primary-900">{status.desks}</strong> desks</span>
      <span class="text-primary-600"><strong class="text-primary-900">{status.stands}</strong> stands</span>
      <span class="text-primary-600"><strong class="text-primary-900">{status.authorized_wasms}</strong> WASMs</span>
      <span class="text-primary-600"><strong class="text-primary-900">{status.events}</strong> events</span>
    </div>
  {/if}

  <!-- Error -->
  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <svg class="w-5 h-5 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      </svg>
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  <!-- Tree -->
  {#if loading && !tree}
    <div class="grid gap-3">
      {#each [1, 2, 3] as n (n)}
        <div class="card p-4">
          <div class="skeleton h-5 w-48 mb-3"></div>
          <div class="skeleton h-4 w-64"></div>
        </div>
      {/each}
    </div>
  {:else if tree && tree.sections.length === 0}
    <div class="text-center py-16">
      <svg class="w-12 h-12 mx-auto text-primary-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 9V18m0 0a3 3 0 11-6 0 3 3 0 016 0zm12-3v6m0 0a3 3 0 11-6 0 3 3 0 016 0zM9 9l12-3" />
      </svg>
      <p class="text-primary-500 text-sm font-medium">No sections yet</p>
      {#if $isAuthenticated}
        <p class="text-primary-400 text-xs mt-1">Create the first section to start orchestrating.</p>
      {:else}
        <p class="text-primary-400 text-xs mt-1">Log in to create sections, desks and stands.</p>
      {/if}
    </div>
  {:else if tree}
    <div class="space-y-4">
      {#each tree.sections as section (section.name)}
        <div class="card overflow-hidden">
          <!-- Section header -->
          <div class="flex items-start justify-between gap-3 p-4 bg-primary-50/60">
            <button class="flex items-start gap-2.5 min-w-0 text-left" onclick={() => toggleSection(section.name)}>
              <svg
                class="w-4 h-4 mt-0.5 text-primary-400 transition-transform shrink-0 {sectionOpen(section.name) ? 'rotate-90' : ''}"
                fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
              <div class="min-w-0">
                <div class="font-semibold text-primary-900 truncate">{section.name}</div>
                {#if section.description}
                  <div class="text-xs text-primary-500 mt-0.5">{section.description}</div>
                {/if}
                {#if section.commander_principal}
                  <div class="text-xs text-primary-400 mt-1 font-mono" title={section.commander_principal}>
                    commander: {shortPrincipal(section.commander_principal)}
                  </div>
                {/if}
                {#if placementLabel(section)}
                  <div class="text-xs text-primary-400 mt-1 font-mono" title={section.subnet || section.subnet_type}>
                    ⬡ {placementLabel(section)}
                  </div>
                {/if}
              </div>
            </button>
            {#if $isAuthenticated}
              <div class="flex items-center gap-1.5 shrink-0">
                <button class="btn-ghost btn-sm" onclick={() => openCreateDesk(section)}>+ Desk</button>
                <button class="btn-ghost btn-sm" onclick={() => openSetCommander({ section: section.name }, section.commander_principal)}>Commander</button>
              </div>
            {/if}
          </div>

          {#if sectionOpen(section.name)}
            <div class="divide-y divide-[var(--color-border-primary)]">
              {#if section.desks.length === 0}
                <div class="px-4 py-3 text-xs text-primary-400">No desks in this section.</div>
              {/if}
              {#each section.desks as desk (desk.name)}
                {@const deskKey = `${section.name}/${desk.name}`}
                <div>
                  <!-- Desk header -->
                  <div class="flex items-start justify-between gap-3 px-4 py-3 pl-6">
                    <button class="flex items-start gap-2.5 min-w-0 text-left" onclick={() => toggleDesk(deskKey)}>
                      <svg
                        class="w-3.5 h-3.5 mt-1 text-primary-400 transition-transform shrink-0 {deskOpen(deskKey) ? 'rotate-90' : ''}"
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                      </svg>
                      <div class="min-w-0">
                        <div class="text-sm font-medium text-primary-800 truncate">{desk.name}</div>
                        {#if desk.description}
                          <div class="text-xs text-primary-400 mt-0.5">{desk.description}</div>
                        {/if}
                        {#if desk.commander_principal}
                          <div class="text-xs text-primary-400 mt-0.5 font-mono" title={desk.commander_principal}>
                            commander: {shortPrincipal(desk.commander_principal)}
                          </div>
                        {/if}
                        {#if placementLabel(desk)}
                          <div class="text-xs text-primary-400 mt-0.5 font-mono" title={desk.subnet || desk.subnet_type}>
                            ⬡ {placementLabel(desk)}
                          </div>
                        {/if}
                      </div>
                    </button>
                    {#if $isAuthenticated}
                      <div class="flex flex-wrap items-center justify-end gap-1.5 shrink-0">
                        <button class="btn-ghost btn-sm" onclick={() => openCreateStand(desk)}>+ Stand</button>
                        <button class="btn-ghost btn-sm" onclick={() => openRegisterStand(desk)}>Register</button>
                        <button class="btn-ghost btn-sm" onclick={() => openUpgradeDesk(desk)}>Upgrade</button>
                        <button class="btn-ghost btn-sm" onclick={() => openSetCommander({ desk: desk.name }, desk.commander_principal)}>Commander</button>
                      </div>
                    {/if}
                  </div>

                  {#if deskOpen(deskKey)}
                    <div class="px-4 pb-3 pl-12 space-y-2">
                      {#if desk.stands.length === 0}
                        <div class="text-xs text-primary-400 py-1">No stands in this desk.</div>
                      {/if}
                      {#each desk.stands as stand (stand.name)}
                        <div class="rounded-lg border border-[var(--color-border-primary)] bg-white p-3">
                          <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                            <div class="min-w-0 space-y-1.5">
                              <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-sm font-medium text-primary-900">{stand.name}</span>
                                <span class="badge {stand.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">
                                  {stand.kind}
                                </span>
                                {#if stand.status}
                                  <span class="badge badge-neutral">{stand.status}</span>
                                {/if}
                                {#if stand.subnet}
                                  <span class="badge badge-neutral font-mono" title="subnet {stand.subnet}">⬡ {shortId(stand.subnet)}</span>
                                {/if}
                              </div>
                              <div class="flex items-center gap-2 text-xs">
                                <button
                                  class="font-mono text-primary-600 hover:text-primary-900 transition-colors inline-flex items-center gap-1"
                                  title="Copy canister id"
                                  onclick={() => copy(stand.canister_id)}
                                >
                                  <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3" />
                                  </svg>
                                  {stand.canister_id || '—'}
                                </button>
                                {#if stand.wasm_hash}
                                  <span class="text-primary-400 font-mono" title={stand.wasm_hash}>· {shortHash(stand.wasm_hash)}</span>
                                {/if}
                              </div>
                            </div>
                            <div class="flex flex-wrap items-center sm:justify-end gap-1.5 shrink-0">
                              <button class="btn-ghost btn-sm" onclick={() => toggleStand(stand)}>
                                <svg
                                  class="w-3.5 h-3.5 transition-transform {expandedStands[stand.canister_id] ? 'rotate-180' : ''}"
                                  fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                                >
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                </svg>
                                Details
                              </button>
                              <a
                                href={standLink(stand)}
                                target="_blank"
                                rel="noopener noreferrer"
                                class="btn-secondary btn-sm"
                              >
                                {stand.kind === 'backend' ? 'Candid UI' : 'Open'}
                                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                                </svg>
                              </a>
                              {#if $isAuthenticated}
                                <button class="btn-ghost btn-sm" onclick={() => openUpgradeStand(stand)}>Upgrade</button>
                                <button class="btn-ghost btn-sm" onclick={() => runStandAction('Snapshot', () => createSnapshot(stand.name))}>Snapshot</button>
                                <button class="btn-ghost btn-sm" onclick={() => runStandAction('Revert', () => revertSnapshot(stand.name))}>Revert</button>
                                <button class="btn-ghost btn-sm" onclick={() => runStandAction('Stop', () => stopCanister(stand.name))}>Stop</button>
                                <button class="btn-ghost btn-sm" onclick={() => runStandAction('Start', () => startCanister(stand.name))}>Start</button>
                              {/if}
                            </div>
                          </div>

                          {#if expandedStands[stand.canister_id]}
                            <div class="mt-3 pt-3 border-t border-[var(--color-border-primary)] space-y-3">
                              <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-primary-500">
                                <span>status: <span class="font-medium text-primary-800">{stand.status || '—'}</span></span>
                                <span>wasm: <span class="font-mono">{stand.wasm_key || '—'}</span></span>
                                {#if stand.wasm_hash}
                                  <span class="font-mono" title={stand.wasm_hash}>hash {shortHash(stand.wasm_hash)}</span>
                                {/if}
                                {#if stand.snapshot_id}
                                  <span class="font-mono" title={stand.snapshot_id}>snapshot {shortHash(stand.snapshot_id)}</span>
                                {/if}
                                <span class="font-mono" title={stand.subnet || 'default (conductor subnet)'}>subnet {stand.subnet ? shortId(stand.subnet) : '— default'}</span>
                              </div>

                              {#if standLoading[stand.canister_id]}
                                <div class="skeleton h-4 w-48"></div>
                              {:else}
                                <div>
                                  <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider mb-1.5">Recent events</div>
                                  {#if (standEvents[stand.canister_id]?.length ?? 0) === 0}
                                    <div class="text-xs text-primary-400">No events for this canister.</div>
                                  {:else}
                                    <ul class="space-y-1">
                                      {#each standEvents[stand.canister_id] as e (e.idx)}
                                        <li class="text-xs flex items-start gap-2">
                                          <span class="badge {evBadge(e.btype)} shrink-0">{e.btype}</span>
                                          <span class="text-primary-600 break-words min-w-0">{evSummary(e)}</span>
                                        </li>
                                      {/each}
                                    </ul>
                                  {/if}
                                </div>

                                <div>
                                  <div class="flex items-center justify-between mb-1.5">
                                    <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider">Canister logs</div>
                                    <button class="text-xs text-primary-500 hover:text-primary-800" onclick={() => loadStandDetails(stand.canister_id)}>Reload</button>
                                  </div>
                                  {#if standLogErr[stand.canister_id]}
                                    <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
                                      Couldn't fetch logs: {standLogErr[stand.canister_id]}
                                      {#if $isAuthenticated}
                                        <button class="underline ml-1 font-medium" onclick={() => makeLogsPublic(stand)}>Make logs public</button>
                                      {/if}
                                    </div>
                                  {:else if (standLogs[stand.canister_id]?.length ?? 0) === 0}
                                    <div class="text-xs text-primary-400">No logs recorded yet.</div>
                                  {:else}
                                    <pre class="text-[11px] leading-relaxed font-mono bg-primary-900 text-primary-100 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap">{#each standLogs[stand.canister_id] as r (r.idx)}<span class="text-primary-400">{fmtTs(r.timestamp_nanos)}</span>  {r.content}
{/each}</pre>
                                  {/if}
                                </div>

                                {#if stand.kind === 'backend'}
                                  <div>
                                    <div class="flex items-center justify-between mb-1.5">
                                      <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider">Inspect (Basilisk)</div>
                                      <button class="text-xs text-primary-500 hover:text-primary-800 disabled:opacity-40" disabled={browseBusy[stand.canister_id]} onclick={() => runBrowse(stand)}>
                                        {browseBusy[stand.canister_id] ? 'Loading…' : 'Browse data'}
                                      </button>
                                    </div>
                                    {#if browseErr[stand.canister_id]}
                                      <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">{browseErr[stand.canister_id]}</div>
                                    {:else if browseData[stand.canister_id] !== undefined}
                                      <pre class="text-[11px] leading-relaxed font-mono bg-primary-50 text-primary-800 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap">{JSON.stringify(browseData[stand.canister_id], null, 2)}</pre>
                                    {:else}
                                      <div class="text-xs text-primary-400">Read-only view of the stand's stable data. Click “Browse data”.</div>
                                    {/if}
                                  </div>

                                  {#if $isAuthenticated}
                                    <div>
                                      <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider mb-1.5">Python console (Basilisk)</div>
                                      <textarea
                                        class="input font-mono text-[11px] w-full min-h-[64px] resize-y"
                                        placeholder={'print(1 + 1)\nimport sys; print(sys.version)'}
                                        bind:value={consoleCode[stand.canister_id]}
                                        onkeydown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runExec(stand); } }}
                                      ></textarea>
                                      <div class="flex items-center justify-between mt-1.5">
                                        <span class="text-[11px] text-primary-400">Runs server-side via <span class="font-mono">__shell__</span>. ⌘/Ctrl+Enter to run.</span>
                                        <button class="btn-secondary btn-sm" disabled={consoleBusy[stand.canister_id]} onclick={() => runExec(stand)}>
                                          {consoleBusy[stand.canister_id] ? 'Running…' : 'Run'}
                                        </button>
                                      </div>
                                      {#if consoleErr[stand.canister_id]}
                                        <div class="text-xs text-red-700 bg-red-50 border border-red-200 rounded-md px-2 py-1.5 mt-1.5">{consoleErr[stand.canister_id]}</div>
                                      {:else if consoleOut[stand.canister_id] !== undefined}
                                        <pre class="text-[11px] leading-relaxed font-mono bg-primary-900 text-primary-100 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap mt-1.5">{consoleOut[stand.canister_id] || '(no output)'}</pre>
                                      {/if}
                                    </div>
                                  {/if}
                                {/if}
                              {/if}
                            </div>
                          {/if}
                        </div>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

{#if modal}
  <FormModal
    title={modal.title}
    description={modal.description}
    fields={modal.fields}
    submitLabel={modal.submitLabel}
    busy={modalBusy}
    onsubmit={submitModal}
    oncancel={closeModal}
  />
{/if}
