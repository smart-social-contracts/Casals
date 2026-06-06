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
    renameSection,
    renameDesk,
    renameStand,
    deleteSection,
    deleteDesk,
    deleteStand,
  } from '$lib/api';
  import type {
    Tree, Status, Section, Desk, Stand, UpdateResult,
    OrchestrationEvent, CanisterLogRecord, AuthorizedWasm,
  } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import FormModal from '$lib/components/FormModal.svelte';
  import OrchestraDiagram from '$lib/components/OrchestraDiagram.svelte';
  import type { Field } from '$lib/components/FormModal.svelte';

  type OrchestraView = 'tree' | 'diagram';

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

  let overviewOpen = $state(false);
  let orchestraView = $state<OrchestraView>('tree');
  let filterQuery = $state('');

  // Filtered view of the tree. A section is kept when any of its desks match,
  // a desk is kept when any of its stands match or the desk itself matches.
  // Matching is case-insensitive substring against name, canister ID, WASM key,
  // status, description, commander, subnet.
  const filteredTree = $derived.by(() => {
    if (!tree) return null;
    const q = filterQuery.trim().toLowerCase();
    if (!q) return tree;
    const matchStand = (s: Stand) =>
      [s.name, s.canister_id, s.wasm_key, s.wasm_hash, s.status, s.kind, s.subnet]
        .some((v) => (v ?? '').toLowerCase().includes(q));
    const matchDesk = (d: Desk) =>
      [d.name, d.description, d.commander_principal, d.subnet, d.subnet_type]
        .some((v) => (v ?? '').toLowerCase().includes(q));
    const matchSection = (sec: Section) =>
      [sec.name, sec.description, sec.commander_principal, sec.subnet, sec.subnet_type]
        .some((v) => (v ?? '').toLowerCase().includes(q));
    const sections = tree.sections
      .map((sec) => {
        const desks = sec.desks
          .map((dk) => {
            const stands = dk.stands.filter(matchStand);
            if (stands.length || matchDesk(dk)) return { ...dk, stands };
            return null;
          })
          .filter(Boolean) as Desk[];
        if (desks.length || matchSection(sec)) return { ...sec, desks };
        return null;
      })
      .filter(Boolean) as Section[];
    return { ...tree, sections };
  });

  let modal = $state<ModalConfig | null>(null);
  let modalBusy = $state(false);
  let modalLogLines = $state<string[]>([]);

  // While an operation is running, poll the event log and stream new entries
  // into the modal's live log window so the user can see progress.
  let _logPollTimer: ReturnType<typeof setInterval> | null = null;
  let _logLastSeen = $state(0); // timestamp_secs of last event already shown

  function _eventToLogLine(ev: OrchestrationEvent): string {
    const tid = ev.canister_id ? ` [${ev.canister_id.slice(0, 8)}…]` : '';
    const payload = ev.payload && typeof ev.payload === 'object'
      ? Object.entries(ev.payload as Record<string, unknown>)
          .filter(([k]) => k !== 'desk')
          .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
          .join(' ')
      : '';
    return `${ev.kind}${tid}${payload ? '  ' + payload : ''}`;
  }

  function _startLogPoll() {
    _logLastSeen = Math.floor(Date.now() / 1000) - 2; // capture events from now
    modalLogLines = [];
    _logPollTimer = setInterval(async () => {
      try {
        const evs = await getEvents({ take: 30 });
        const fresh = evs.filter((e) => (e.timestamp_secs ?? 0) > _logLastSeen);
        if (fresh.length) {
          _logLastSeen = Math.max(...fresh.map((e) => e.timestamp_secs ?? 0));
          modalLogLines = [...modalLogLines, ...fresh.map(_eventToLogLine)];
        }
      } catch { /* ignore poll errors */ }
    }, 1200);
  }

  function _stopLogPoll() {
    if (_logPollTimer !== null) { clearInterval(_logPollTimer); _logPollTimer = null; }
  }

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
    // Auto-expand when a filter is active so matches are visible.
    if (filterQuery.trim()) return true;
    return expandedSections[name] !== false;
  }
  function deskOpen(key: string): boolean {
    if (filterQuery.trim()) return true;
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
    _startLogPoll();
    try {
      await modal.onsubmit(values);
      toasts.success(`${modal.title} succeeded`);
      modal = null;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Operation failed');
    } finally {
      _stopLogPoll();
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

  // ── Rename / Delete ───────────────────────────────────────────────────────
  function openRenameSection(section: Section) {
    openModal({
      title: `Rename section`,
      fields: [
        { name: 'new_name', label: 'New name', required: true, value: section.name },
        { name: 'description', label: 'Description', value: section.description ?? '' },
      ],
      submitLabel: 'Rename',
      onsubmit: (v) => renameSection({ section: section.name, new_name: String(v.new_name).trim(), description: String(v.description) }),
    });
  }

  function openRenameDesk(desk: Desk) {
    openModal({
      title: `Rename desk`,
      fields: [
        { name: 'new_name', label: 'New name', required: true, value: desk.name },
        { name: 'description', label: 'Description', value: desk.description ?? '' },
      ],
      submitLabel: 'Rename',
      onsubmit: (v) => renameDesk({ desk: desk.name, new_name: String(v.new_name).trim(), description: String(v.description) }),
    });
  }

  function openRenameStand(stand: Stand) {
    openModal({
      title: `Rename stand`,
      fields: [{ name: 'new_name', label: 'New name', required: true, value: stand.name }],
      submitLabel: 'Rename',
      onsubmit: (v) => renameStand({ stand: stand.name, new_name: String(v.new_name).trim() }),
    });
  }

  function openDeleteSection(section: Section) {
    openModal({
      title: `Delete section "${section.name}"`,
      description: 'All desks and stands will be removed. Canisters are returned to the pool (not deleted).',
      fields: [
        { name: 'confirm', label: `Type "${section.name}" to confirm`, required: true },
      ],
      submitLabel: 'Delete section',
      onsubmit: async (v) => {
        if (String(v.confirm).trim() !== section.name) throw new Error('Name does not match');
        return deleteSection({ section: section.name });
      },
    });
  }

  function openDeleteDesk(desk: Desk) {
    openModal({
      title: `Delete desk "${desk.name}"`,
      description: 'All stands will be removed. Canisters are returned to the pool (not deleted).',
      fields: [
        { name: 'confirm', label: `Type "${desk.name}" to confirm`, required: true },
      ],
      submitLabel: 'Delete desk',
      onsubmit: async (v) => {
        if (String(v.confirm).trim() !== desk.name) throw new Error('Name does not match');
        return deleteDesk({ desk: desk.name });
      },
    });
  }

  function openDeleteStand(stand: Stand) {
    openModal({
      title: `Delete stand "${stand.name}"`,
      description: 'The stand record is removed and its canister returned to the pool (not deleted).',
      fields: [
        { name: 'confirm', label: `Type "${stand.name}" to confirm`, required: true },
      ],
      submitLabel: 'Delete stand',
      onsubmit: async (v) => {
        if (String(v.confirm).trim() !== stand.name) throw new Error('Name does not match');
        return deleteStand({ stand: stand.name });
      },
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
    const familyOpts = allFamilies.map((f) => ({ value: f, label: f }));
    openModal({
      title: 'Deploy desk',
      description: `Deploy all stands in desk "${desk.name}"`,
      fields: [
        opts.length > 0
          ? { name: 'wasm_key', label: 'Version', type: 'select', value: fams[0], options: opts }
          : familyOpts.length > 0
            ? { name: 'wasm_key', label: 'WASM family', type: 'select',
                value: fams[0] || familyOpts[0].value, options: familyOpts }
            : { name: 'wasm_key', label: 'WASM key', required: true, placeholder: 'hello-world-basilisk' },
        {
          name: 'reinstall',
          type: 'checkbox',
          label: 'Reinstall (wipes all stand state — data will be lost)',
          value: false,
          help: '⚠️ Reinstall erases the canister state. Use only if you intentionally want a clean slate.',
        },
      ],
      submitLabel: 'Deploy',
      onsubmit: (v) => upgradeTo({
        desk: desk.name,
        wasm_key: String(v.wasm_key).trim(),
        reinstall: Boolean(v.reinstall),
      }),
    });
  }

  function openUpgradeStand(stand: Stand) {
    const family = familyOf(stand.wasm_key);
    const opts = versionOptions(family);
    const familyOpts = allFamilies.map((f) => ({ value: f, label: f }));
    openModal({
      title: 'Deploy stand',
      description: `Deploy stand "${stand.name}"${family ? ` · family ${family}` : ''}`,
      fields: [
        opts.length > 0
          ? { name: 'wasm_key', label: 'Version', type: 'select', value: family, options: opts }
          : familyOpts.length > 0
            ? { name: 'wasm_key', label: 'WASM family', type: 'select',
                value: family || familyOpts[0].value, options: familyOpts }
            : { name: 'wasm_key', label: 'WASM key', required: true, value: stand.wasm_key ?? '',
                placeholder: 'hello-world-basilisk' },
        {
          name: 'reinstall',
          type: 'checkbox',
          label: 'Reinstall (wipes stand state — data will be lost)',
          value: false,
          help: '⚠️ Reinstall erases the canister state. Use only if you intentionally want a clean slate.',
        },
      ],
      submitLabel: 'Deploy',
      onsubmit: (v) => upgradeTo({
        stand: stand.name,
        wasm_key: String(v.wasm_key).trim(),
        reinstall: Boolean(v.reinstall),
      }),
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

  <!-- Filter bar + Overview (single card) -->
  {#if tree}
    <div class="card overflow-hidden">
      <!-- Filter row -->
      <div class="flex items-center gap-0">
        <!-- Search icon -->
        <svg class="w-4 h-4 text-primary-400 shrink-0 ml-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
        </svg>
        <!-- Input — no pl-* hack needed, icon is a sibling not absolute -->
        <input
          type="text"
          class="flex-1 px-3 py-2.5 text-sm bg-transparent border-0 outline-none placeholder:text-primary-300 text-primary-900"
          placeholder="Filter by name, canister ID, WASM, status…"
          bind:value={filterQuery}
        />
        <!-- Clear button -->
        {#if filterQuery}
          <button
            type="button"
            class="text-primary-300 hover:text-primary-500 mr-1 p-1 rounded"
            aria-label="Clear filter"
            onclick={() => (filterQuery = '')}
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        {/if}
        <div class="w-px h-5 bg-primary-100 mx-1 shrink-0"></div>
        <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden shrink-0 mr-1" role="group" aria-label="Orchestra view">
          <button
            type="button"
            class="px-2.5 py-2 text-xs font-medium inline-flex items-center gap-1 {orchestraView === 'tree' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
            aria-pressed={orchestraView === 'tree'}
            onclick={() => (orchestraView = 'tree')}
          >
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM3.75 12h.007v.008H3.75V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm-.375 5.25h.007v.008H3.75v-.008zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
            </svg>
            Tree
          </button>
          <button
            type="button"
            class="px-2.5 py-2 text-xs font-medium inline-flex items-center gap-1 {orchestraView === 'diagram' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
            aria-pressed={orchestraView === 'diagram'}
            onclick={() => (orchestraView = 'diagram')}
          >
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 016 0h12a2.25 2.25 0 012.25 2.25v12A2.25 2.25 0 0118 18H6a2.25 2.25 0 01-2.25-2.25V6zM8.25 6.75v10.5M15.75 6.75v10.5" />
            </svg>
            Diagram
          </button>
        </div>
        <!-- Divider + Overview toggle -->
        {#if status}
          <div class="w-px h-5 bg-primary-100 mx-1 shrink-0"></div>
          <button
            type="button"
            class="shrink-0 flex items-center gap-1 text-xs text-primary-400 hover:text-primary-600 transition-colors px-3 py-2.5"
            onclick={() => (overviewOpen = !overviewOpen)}
            aria-expanded={overviewOpen}
          >
            <span class="uppercase tracking-wider font-semibold">Overview</span>
            <svg class="w-3 h-3 transition-transform duration-200 {overviewOpen ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        {/if}
      </div>

      <!-- Overview detail grid — expands inline, no separate card -->
      {#if status && overviewOpen}
        <div class="border-t border-primary-100 px-4 py-3 grid grid-cols-3 sm:grid-cols-6 gap-x-6 gap-y-3 text-sm">
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Version</span>
            <span class="font-mono text-primary-800">v{status.version}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Sections</span>
            <span class="font-semibold text-primary-900">{status.sections}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Desks</span>
            <span class="font-semibold text-primary-900">{status.desks}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Stands</span>
            <span class="font-semibold text-primary-900">{status.stands}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">WASMs</span>
            <span class="font-semibold text-primary-900">{status.authorized_wasms}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Events</span>
            <span class="font-semibold text-primary-900">{status.events}</span>
          </div>
        </div>
      {/if}
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

  <!-- Tree / diagram -->
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
  {:else if filteredTree}
    {#if filterQuery && filteredTree.sections.length === 0}
      <div class="text-center py-10 text-primary-400 text-sm">No results for <strong class="text-primary-700">"{filterQuery}"</strong></div>
    {:else if orchestraView === 'diagram'}
      <div class="card p-5">
        <OrchestraDiagram tree={filteredTree} />
      </div>
    {/if}
    {#if orchestraView === 'tree'}
    <div class="space-y-4">
      {#each filteredTree.sections as section (section.name)}
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
              <div class="flex items-center gap-0.5 shrink-0">
                <button class="icon-btn" title="Add desk" onclick={() => openCreateDesk(section)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
                </button>
                <button class="icon-btn" title="Set commander" onclick={() => openSetCommander({ section: section.name }, section.commander_principal)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0"/></svg>
                </button>
                <button class="icon-btn" title="Rename section" onclick={() => openRenameSection(section)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                </button>
                <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" title="Delete section" onclick={() => openDeleteSection(section)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                </button>
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
                      <div class="flex items-center gap-0.5 shrink-0">
                        <button class="icon-btn" title="Add stand" onclick={() => openCreateStand(desk)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
                        </button>
                        <button class="icon-btn" title="Register existing canister" onclick={() => openRegisterStand(desk)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244"/></svg>
                        </button>
                        <button class="icon-btn" title="Deploy all stands in desk" onclick={() => openUpgradeDesk(desk)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"/></svg>
                        </button>
                        <button class="icon-btn" title="Set commander" onclick={() => openSetCommander({ desk: desk.name }, desk.commander_principal)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0"/></svg>
                        </button>
                        <button class="icon-btn" title="Rename desk" onclick={() => openRenameDesk(desk)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                        </button>
                        <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" title="Delete desk" onclick={() => openDeleteDesk(desk)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                        </button>
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
                            <div class="flex items-center gap-0.5 shrink-0">
                              <!-- Details toggle -->
                              <button class="icon-btn" title="Toggle details" onclick={() => toggleStand(stand)}>
                                <svg class="w-4 h-4 transition-transform {expandedStands[stand.canister_id] ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/>
                                </svg>
                              </button>
                              <!-- Open / Candid UI -->
                              <a
                                href={standLink(stand)}
                                target="_blank"
                                rel="noopener noreferrer"
                                class="icon-btn"
                                title={stand.kind === 'backend' ? 'Open Candid UI' : 'Open frontend'}
                              >
                                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/>
                                </svg>
                              </a>
                              {#if $isAuthenticated}
                                <!-- Deploy -->
                                <button class="icon-btn" title="Deploy (upgrade WASM)" onclick={() => openUpgradeStand(stand)}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"/></svg>
                                </button>
                                <!-- Snapshot -->
                                <button class="icon-btn" title="Create snapshot" onclick={() => runStandAction('Snapshot', () => createSnapshot(stand.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.776 48.776 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"/></svg>
                                </button>
                                <!-- Revert -->
                                <button class="icon-btn" title="Revert to snapshot" onclick={() => runStandAction('Revert', () => revertSnapshot(stand.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3"/></svg>
                                </button>
                                <!-- Stop -->
                                <button class="icon-btn" title="Stop canister" onclick={() => runStandAction('Stop', () => stopCanister(stand.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="1" stroke-linecap="round" stroke-linejoin="round"/></svg>
                                </button>
                                <!-- Start -->
                                <button class="icon-btn" title="Start canister" onclick={() => runStandAction('Start', () => startCanister(stand.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/></svg>
                                </button>
                                <!-- Rename -->
                                <button class="icon-btn" title="Rename stand" onclick={() => openRenameStand(stand)}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                                </button>
                                <!-- Delete -->
                                <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" title="Delete stand" onclick={() => openDeleteStand(stand)}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                                </button>
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
  {/if}
</div>

{#if modal}
  <FormModal
    title={modal.title}
    description={modal.description}
    fields={modal.fields}
    submitLabel={modal.submitLabel}
    busy={modalBusy}
    logLines={modalLogLines}
    onsubmit={submitModal}
    oncancel={closeModal}
  />
{/if}
