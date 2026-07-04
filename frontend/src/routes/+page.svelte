<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getTree,
    getStatus,
    createSection,
    createStand,
    registerCanister,
    setCommander,
    upgradeTo,
    createSnapshot,
    revertSnapshot,
    stopCanister,
    startCanister,
    setLogVisibility,
    getEvents,
    getCanisterLogs,
    getCanisterDeployment,
    getCyclesCached,
    listAuthorizedWasms,
    refreshControllersCache,
    canisterBrowse,
    canisterExec,
    shortHash,
    shortPrincipal,
    canisterLink,
    formatCycles,
    formatIsoTs,
    cyclesIsLow,
    renameSection,
    renameStand,
    renameCanister,
    deleteSection,
    deleteStand,
    deleteCanister,
    destroyCanister,
    backendCanisterId,
  } from '$lib/api';
  import type {
    Tree, Status, Section, Stand, Canister, UpdateResult,
    OrchestrationEvent, CanisterLogRecord, AuthorizedWasm,
    CanisterCycles, CanisterDeployment, IcRunStatus,
  } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import FormModal from '$lib/components/FormModal.svelte';
  import CreateCanisterModal from '$lib/components/CreateCanisterModal.svelte';
  import OrchestraDiagram from '$lib/components/OrchestraDiagram.svelte';
  import SubnetFlags from '$lib/components/SubnetFlags.svelte';
  import CanisterControllersBadge from '$lib/components/CanisterControllersBadge.svelte';
  import CanisterTypeBadges from '$lib/components/CanisterTypeBadges.svelte';
  import { warmSubnetGeoCache } from '$lib/subnetGeo';
  import { governanceConsoleUrl } from '$lib/orchestrationNav';
  import { resolveWasmType, hasBasiliskFeatures } from '$lib/canisterTypes';
  import { familyOf, versionOptions } from '$lib/createCanisterForm';
  import { buildPrincipalLabels, controllerLabel } from '$lib/controllerLabels';
  import type { Field } from '$lib/components/FormModal.svelte';

  type OrchestraView = 'tree' | 'diagram';

  type Values = Record<string, string | boolean>;

  interface ModalConfig {
    title: string;
    description?: string;
    fields: Field[];
    submitLabel: string;
    danger?: boolean;
    onsubmit: (values: Values) => Promise<UpdateResult>;
  }

  let tree = $state<Tree | null>(null);
  let status = $state<Status | null>(null);
  let loading = $state(true);
  let error = $state('');
  let catalog = $state<AuthorizedWasm[]>([]);

  let expandedSections = $state<Record<string, boolean>>({});
  let expandedStands = $state<Record<string, boolean>>({});

  // Per-canister expandable detail panel (status + recent events + canister logs).
  let expandedCanisters = $state<Record<string, boolean>>({});
  let canisterEvents = $state<Record<string, OrchestrationEvent[]>>({});
  let canisterLogs = $state<Record<string, CanisterLogRecord[]>>({});
  let canisterLogErr = $state<Record<string, string>>({});
  let canisterLoading = $state<Record<string, boolean>>({});
  let canisterCycles = $state<Record<string, CanisterCycles>>({});
  let canisterDeployment = $state<Record<string, CanisterDeployment | null>>({});
  let canisterDetailsErr = $state<Record<string, string>>({});

  // Basilisk introspection (only for canisters built with __basilisk_features__).
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

  const principalLabels = $derived.by(() => buildPrincipalLabels(tree, backendCanisterId()));

  // Filtered view of the tree. A section is kept when any of its stands match,
  // a stand is kept when any of its canisters match or the stand itself matches.
  // Matching is case-insensitive substring against name, canister ID, WASM key,
  // status, description, commander, subnet.
  const filteredTree = $derived.by(() => {
    if (!tree) return null;
    const q = filterQuery.trim().toLowerCase();
    if (!q) return tree;
    const matchCanister = (s: Canister) =>
      [s.name, s.canister_id, s.wasm_key, s.wasm_hash, s.status, s.kind, s.subnet]
        .some((v) => (v ?? '').toLowerCase().includes(q));
    const matchStand = (d: Stand) =>
      [d.name, d.description, d.commander_principal, d.subnet, d.subnet_type]
        .some((v) => (v ?? '').toLowerCase().includes(q));
    const matchSection = (sec: Section) =>
      [sec.name, sec.description, sec.commander_principal, sec.subnet, sec.subnet_type]
        .some((v) => (v ?? '').toLowerCase().includes(q));
    const sections = tree.sections
      .map((sec) => {
        const stands = sec.stands
          .map((dk) => {
            const canisters = dk.canisters.filter(matchCanister);
            if (canisters.length || matchStand(dk)) return { ...dk, canisters };
            return null;
          })
          .filter(Boolean) as Stand[];
        if (stands.length || matchSection(sec)) return { ...sec, stands };
        return null;
      })
      .filter(Boolean) as Section[];
    return { ...tree, sections };
  });

  let modal = $state<ModalConfig | null>(null);
  let modalBusy = $state(false);
  let modalLogLines = $state<string[]>([]);
  let createCanisterStand = $state<Stand | null>(null);

  // While an operation is running, poll the event log and stream new entries
  // into the modal's live log window so the user can see progress.
  let _logPollTimer: ReturnType<typeof setInterval> | null = null;
  let _logLastSeen = $state(0); // timestamp_secs of last event already shown

  function _eventToLogLine(ev: OrchestrationEvent): string {
    const tid = ev.canister_id ? ` [${ev.canister_id.slice(0, 8)}…]` : '';
    const payload = ev.payload && typeof ev.payload === 'object'
      ? Object.entries(ev.payload as Record<string, unknown>)
          .filter(([k]) => k !== 'stand')
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

  function collectSubnetIds(t: Tree | null): string[] {
    if (!t) return [];
    const ids = new Set<string>();
    for (const sec of t.sections) {
      if (sec.subnet) ids.add(sec.subnet);
      for (const stand of sec.stands) {
        if (stand.subnet) ids.add(stand.subnet);
        for (const c of stand.canisters) {
          if (c.subnet) ids.add(c.subnet);
        }
      }
    }
    return [...ids];
  }

  async function load() {
    loading = true;
    error = '';
    canisterCycles = {};
    try {
      const missingControllers =
        !tree ||
        tree.sections.some((sec) =>
          sec.stands.some((stand) =>
            stand.canisters.some((c) => c.canister_id && !(c.controllers?.length)),
          ),
        );
      if (missingControllers) {
        await refreshControllersCache().catch(() => undefined);
      }
      [tree, status, catalog] = await Promise.all([
        getTree(),
        getStatus(),
        listAuthorizedWasms().catch(() => [] as AuthorizedWasm[]),
      ]);
      void warmSubnetGeoCache(collectSubnetIds(tree));
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  // All distinct families in the catalog (for free-text fallback datalist).
  const allFamilies = $derived(
    [...new Set(catalog.map((w) => w.family || w.key.split('@')[0]).filter(Boolean))].sort()
  );

  function shortId(id: string): string {
    if (!id) return '';
    return id.length > 13 ? `${id.slice(0, 5)}…${id.slice(-5)}` : id;
  }

  // Desired-placement label for a section/stand (target for new canisters).
  function placementLabel(o: { subnet?: string; subnet_type?: string }): string {
    if (o.subnet) return `subnet ${shortId(o.subnet)}`;
    if (o.subnet_type) return `subnet type: ${o.subnet_type}`;
    return '';
  }

  // Families present in a stand (across its canisters), for stand-wide upgrades.
  function standFamilies(stand: Stand): string[] {
    const fams = new Set<string>();
    for (const s of stand.canisters) {
      const f = familyOf(s.wasm_key);
      if (f) fams.add(f);
    }
    return [...fams];
  }

  onMount(() => {
    load();
    ensureCyclesCache();
  });

  function sectionOpen(name: string): boolean {
    // Auto-expand when a filter is active so matches are visible.
    if (filterQuery.trim()) return true;
    return expandedSections[name] !== false;
  }
  function standOpen(key: string): boolean {
    if (filterQuery.trim()) return true;
    return expandedStands[key] !== false;
  }
  function toggleSection(name: string) {
    expandedSections[name] = !sectionOpen(name);
  }
  function toggleStand(key: string) {
    expandedStands[key] = !standOpen(key);
  }

  async function ensureCyclesCache() {
    if (Object.keys(canisterCycles).length) return;
    try {
      const cached = await getCyclesCached();
      if (!cached?.canisters) return;
      const map: Record<string, CanisterCycles> = {};
      for (const row of cached.canisters) map[row.canister_id] = row;
      canisterCycles = map;
    } catch {
      /* cycles snapshot optional — details still show without it */
    }
  }

  function runtimeLabel(status: IcRunStatus | undefined): string {
    if (!status || status === 'unknown') return '—';
    return status;
  }

  function balanceLabel(row: CanisterCycles | undefined): { text: string; low: boolean | null } {
    if (!row) return { text: '—', low: null };
    const low = cyclesIsLow(row.status);
    if (low === false) return { text: 'ok', low: false };
    if (low === true) return { text: 'low balance', low: true };
    return { text: row.error ? 'unknown' : '—', low: null };
  }

  async function toggleCanister(canister: Canister) {
    const cid = canister.canister_id;
    if (!cid) return;
    const open = !expandedCanisters[cid];
    expandedCanisters[cid] = open;
    if (open) await loadCanisterDetails(cid);
  }

  async function loadCanisterDetails(cid: string) {
    canisterLoading[cid] = true;
    canisterLogErr[cid] = '';
    canisterDetailsErr[cid] = '';
    try {
      // The audit log lives in Casals; runtime logs are fetched straight from
      // the management canister (only works if log_visibility is public).
      const [evs, logs, deployment] = await Promise.all([
        getEvents({ canister_id: cid, take: 8 }),
        getCanisterLogs(cid).catch((e: any) => {
          canisterLogErr[cid] = e?.message ?? String(e);
          return [] as CanisterLogRecord[];
        }),
        getCanisterDeployment(cid).catch((e: any) => {
          canisterDetailsErr[cid] = e?.message ?? String(e);
          return null;
        }),
      ]);
      canisterEvents[cid] = evs;
      canisterLogs[cid] = logs;
      canisterDeployment[cid] = deployment;
      await ensureCyclesCache();
    } catch (e: any) {
      canisterDetailsErr[cid] = e?.message ?? String(e);
    } finally {
      canisterLoading[cid] = false;
    }
  }

  async function makeLogsPublic(canister: Canister) {
    try {
      await setLogVisibility({ canister: canister.name, public: true });
      toasts.success('Logs set to public');
      await loadCanisterDetails(canister.canister_id);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to set log visibility');
    }
  }

  async function runBrowse(canister: Canister, query?: Record<string, unknown>) {
    const cid = canister.canister_id;
    browseBusy[cid] = true;
    browseErr[cid] = '';
    try {
      const r = await canisterBrowse(canister.name, query);
      browseData[cid] = r.result;
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      browseErr[cid] = /__browse__|method|not found|no query method/i.test(msg)
        ? 'This canister does not expose introspection (rebuild with __basilisk_features__).'
        : msg;
    } finally {
      browseBusy[cid] = false;
    }
  }

  async function runExec(canister: Canister) {
    const cid = canister.canister_id;
    const code = consoleCode[cid] ?? '';
    if (!code.trim()) return;
    consoleBusy[cid] = true;
    consoleErr[cid] = '';
    try {
      const r = await canisterExec(canister.name, code);
      consoleOut[cid] = r.output ?? '';
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      consoleErr[cid] = /__shell__|method|not found/i.test(msg)
        ? 'This canister does not expose a Python shell (rebuild with __basilisk_features__).'
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
      case 'canister_created': return `${p.name ?? ''} (${p.wasm_key ?? ''})`;
      case 'canister_reinstalled': return `${p.name ?? ''} (${p.wasm_key ?? ''})`;
      case 'assets_uploaded': return `${p.bytes ?? 0} bytes`;
      case 'cycles_topup': return `+${p.amount ?? ''}`;
      case 'cycles_return': return `−${p.amount ?? ''}`;
      case 'create_failed': return 'module hash mismatch';
      default: { const k = Object.keys(p); return k.length ? JSON.stringify(p).slice(0, 80) : ''; }
    }
  }

  async function copy(text: string) {
    if (await copyText(text)) toasts.info('Copied to clipboard');
    else toasts.error('Could not copy');
  }

  function governanceConsoleLabel(canister: Canister): string {
    const t = resolveWasmType(canister);
    if (t === 'baton') return 'Baton';
    if (t === 'multisig') return 'Multisig';
    return 'Console';
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

  // Direct (no extra fields) per-canister lifecycle action.
  async function runCanisterAction(label: string, fn: () => Promise<UpdateResult>) {
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

  function openCreateStand(section: Section) {
    openModal({
      title: 'Create stand',
      description: `In section "${section.name}"`,
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'Agora' },
        { name: 'description', label: 'Description', type: 'textarea' },
        { name: 'commander_principal', label: 'Commander principal', placeholder: 'aaaaa-aa' },
      ],
      submitLabel: 'Create stand',
      onsubmit: (v) => createStand({ ...(clean(v) as any), section: section.name }),
    });
  }

  function openRegisterCanister(stand: Stand) {
    openModal({
      title: 'Register canister',
      description: `In stand "${stand.name}" — registers an existing canister`,
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
      submitLabel: 'Register canister',
      onsubmit: (v) => registerCanister({ ...(clean(v) as any), stand: stand.name }),
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

  function openRenameStand(stand: Stand) {
    openModal({
      title: `Rename stand`,
      fields: [
        { name: 'new_name', label: 'New name', required: true, value: stand.name },
        { name: 'description', label: 'Description', value: stand.description ?? '' },
      ],
      submitLabel: 'Rename',
      onsubmit: (v) => renameStand({ stand: stand.name, new_name: String(v.new_name).trim(), description: String(v.description) }),
    });
  }

  function openRenameCanister(canister: Canister) {
    openModal({
      title: `Rename canister`,
      fields: [{ name: 'new_name', label: 'New name', required: true, value: canister.name }],
      submitLabel: 'Rename',
      onsubmit: (v) => renameCanister({ canister: canister.name, new_name: String(v.new_name).trim() }),
    });
  }

  function openDeleteSection(section: Section) {
    openModal({
      title: `Delete section "${section.name}"`,
      description: 'All stands and canisters will be removed. Canisters are returned to the pool (not deleted).',
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

  function openDeleteStand(stand: Stand) {
    openModal({
      title: `Delete stand "${stand.name}"`,
      description: 'All canisters will be removed. Canisters are returned to the pool (not deleted).',
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

  function openDeleteCanister(canister: Canister) {
    openModal({
      title: `Delete canister "${canister.name}"`,
      description: 'The canister record is removed and its canister returned to the pool (not deleted).',
      fields: [
        { name: 'confirm', label: `Type "${canister.name}" to confirm`, required: true },
      ],
      submitLabel: 'Delete canister',
      onsubmit: async (v) => {
        if (String(v.confirm).trim() !== canister.name) throw new Error('Name does not match');
        return deleteCanister({ canister: canister.name });
      },
    });
  }

  function openDestroyCanister(canister: Canister) {
    openModal({
      title: `Destroy canister "${canister.name}"`,
      description: 'Permanently deletes this canister on the Internet Computer. Remaining cycles are returned to the Casals treasury. This cannot be undone.',
      fields: [
        {
          name: 'ack',
          label: 'I understand this permanently destroys the canister and cannot be undone',
          type: 'checkbox',
          help: 'The canister will be deleted on-chain. Any remaining cycles return to Casals.',
        },
        { name: 'confirm', label: `Type "${canister.name}" to confirm`, required: true },
      ],
      submitLabel: 'Destroy canister',
      danger: true,
      onsubmit: async (v) => {
        if (!v.ack) throw new Error('Please confirm you understand this action is irreversible');
        if (String(v.confirm).trim() !== canister.name) throw new Error('Name does not match');
        return destroyCanister({ canister: canister.name });
      },
    });
  }

  function openSetCommander(target: { section?: string; stand?: string }, current: string) {
    const label = target.section ? `section "${target.section}"` : `stand "${target.stand}"`;
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

  function openCreateCanister(stand: Stand) {
    createCanisterStand = stand;
  }

  function closeCreateCanister() {
    createCanisterStand = null;
  }

  async function onCreateCanisterSuccess() {
    createCanisterStand = null;
    toasts.success('Canister created');
    await load();
  }

  function openUpgradeStand(stand: Stand) {
    const fams = standFamilies(stand);
    const opts = fams.length === 1 ? versionOptions(fams[0], catalog) : [];
    const familyOpts = allFamilies.map((f) => ({ value: f, label: f }));
    openModal({
      title: 'Deploy stand',
      description: `Deploy all canisters in stand "${stand.name}"`,
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
          label: 'Reinstall (wipes all canister state — data will be lost)',
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

  function openUpgradeCanister(canister: Canister) {
    const family = familyOf(canister.wasm_key);
    const opts = versionOptions(family, catalog);
    const familyOpts = allFamilies.map((f) => ({ value: f, label: f }));
    openModal({
      title: 'Deploy canister',
      description: `Deploy canister "${canister.name}"${family ? ` · family ${family}` : ''}`,
      fields: [
        opts.length > 0
          ? { name: 'wasm_key', label: 'Version', type: 'select', value: family, options: opts }
          : familyOpts.length > 0
            ? { name: 'wasm_key', label: 'WASM family', type: 'select',
                value: family || familyOpts[0].value, options: familyOpts }
            : { name: 'wasm_key', label: 'WASM key', required: true, value: canister.wasm_key ?? '',
                placeholder: 'hello-world-basilisk' },
        {
          name: 'reinstall',
          type: 'checkbox',
          label: 'Reinstall (wipes canister state — data will be lost)',
          value: false,
          help: '⚠️ Reinstall erases the canister state. Use only if you intentionally want a clean slate.',
        },
      ],
      submitLabel: 'Deploy',
      onsubmit: (v) => upgradeTo({
        canister: canister.name,
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
      <p class="text-sm text-primary-500 mt-1">Section → Stand → Canister lifecycle orchestration</p>
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
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Stands</span>
            <span class="font-semibold text-primary-900">{status.stands}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Canisters</span>
            <span class="font-semibold text-primary-900">{status.canisters}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">WASMs</span>
            <span class="font-semibold text-primary-900">{status.authorized_wasms}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Events</span>
            <span class="font-semibold text-primary-900">{status.events}</span>
          </div>
          <div class="flex flex-col gap-0.5 sm:col-span-2">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Conductor hosting</span>
            <span class="inline-flex items-center gap-2 text-sm text-primary-700">
              <span class="font-mono text-xs text-primary-500">{shortId(backendCanisterId())}</span>
              <SubnetFlags canisterId={backendCanisterId()} size="md" />
            </span>
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
        <p class="text-primary-400 text-xs mt-1">Log in to create sections, stands and canisters.</p>
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
      {#each filteredTree.sections as section (section.name + '|' + section.id)}
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
                  <div class="flex items-center gap-1.5 flex-wrap text-xs text-primary-400 mt-1 font-mono" title={section.subnet || section.subnet_type}>
                    <span>⬡ {placementLabel(section)}</span>
                    {#if section.subnet}
                      <SubnetFlags subnetId={section.subnet} />
                    {/if}
                  </div>
                {/if}
              </div>
            </button>
            {#if $isAuthenticated}
              <div class="flex items-center gap-0.5 shrink-0">
                <button class="icon-btn" aria-label="Add stand" onclick={() => openCreateStand(section)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
                </button>
                <button class="icon-btn" aria-label="Set commander" onclick={() => openSetCommander({ section: section.name }, section.commander_principal)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0"/></svg>
                </button>
                <button class="icon-btn" aria-label="Rename section" onclick={() => openRenameSection(section)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                </button>
                <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" aria-label="Delete section" onclick={() => openDeleteSection(section)}>
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                </button>
              </div>
            {/if}
          </div>

          {#if sectionOpen(section.name)}
            <div class="divide-y divide-[var(--color-border-primary)]">
              {#if section.stands.length === 0}
                <div class="px-4 py-3 text-xs text-primary-400">No stands in this section.</div>
              {/if}
              {#each section.stands as stand (section.name + '/' + stand.name)}
                {@const standKey = `${section.name}/${stand.name}`}
                <div>
                  <!-- Stand header -->
                  <div class="flex items-start justify-between gap-3 px-4 py-3 pl-6">
                    <button class="flex items-start gap-2.5 min-w-0 text-left" onclick={() => toggleStand(standKey)}>
                      <svg
                        class="w-3.5 h-3.5 mt-1 text-primary-400 transition-transform shrink-0 {standOpen(standKey) ? 'rotate-90' : ''}"
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                      </svg>
                      <div class="min-w-0">
                        <div class="text-sm font-medium text-primary-800 truncate">{stand.name}</div>
                        {#if stand.description}
                          <div class="text-xs text-primary-400 mt-0.5">{stand.description}</div>
                        {/if}
                        {#if stand.commander_principal}
                          <div class="text-xs text-primary-400 mt-0.5 font-mono" title={stand.commander_principal}>
                            commander: {shortPrincipal(stand.commander_principal)}
                          </div>
                        {/if}
                        {#if placementLabel(stand)}
                          <div class="flex items-center gap-1.5 flex-wrap text-xs text-primary-400 mt-0.5 font-mono" title={stand.subnet || stand.subnet_type}>
                            <span>⬡ {placementLabel(stand)}</span>
                            {#if stand.subnet}
                              <SubnetFlags subnetId={stand.subnet} />
                            {/if}
                          </div>
                        {/if}
                      </div>
                    </button>
                    {#if $isAuthenticated}
                      <div class="flex items-center gap-0.5 shrink-0">
                        <button class="icon-btn" aria-label="Add canister" onclick={() => openCreateCanister(stand)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
                        </button>
                        <button class="icon-btn" aria-label="Register existing canister" onclick={() => openRegisterCanister(stand)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244"/></svg>
                        </button>
                        <button class="icon-btn" aria-label="Deploy all canisters in stand" onclick={() => openUpgradeStand(stand)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"/></svg>
                        </button>
                        <button class="icon-btn" aria-label="Set commander" onclick={() => openSetCommander({ stand: stand.name }, stand.commander_principal)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0zM4.501 20.118a7.5 7.5 0 0 1 14.998 0"/></svg>
                        </button>
                        <button class="icon-btn" aria-label="Rename stand" onclick={() => openRenameStand(stand)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                        </button>
                        <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" aria-label="Delete stand" onclick={() => openDeleteStand(stand)}>
                          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                        </button>
                      </div>
                    {/if}
                  </div>

                  {#if standOpen(standKey)}
                    <div class="px-4 pb-3 pl-12 space-y-2">
                      {#if stand.canisters.length === 0}
                        <div class="text-xs text-primary-400 py-1">No canisters in this stand.</div>
                      {/if}
                      {#each stand.canisters as canister (canister.canister_id || canister.name)}
                        <div class="rounded-lg border border-[var(--color-border-primary)] bg-white p-3">
                          <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                            <div class="min-w-0 space-y-1.5">
                              <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-sm font-medium text-primary-900">{canister.name}</span>
                                <span class="badge {canister.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">
                                  {canister.kind}
                                </span>
                                <CanisterTypeBadges {canister} />
                                {#if canister.status}
                                  <span class="badge badge-neutral">{canister.status}</span>
                                {/if}
                                <CanisterControllersBadge
                                  canisterId={canister.canister_id}
                                  controllers={canister.controllers}
                                  {principalLabels}
                                />
                                {#if canister.subnet || canister.canister_id}
                                  <span class="badge badge-neutral font-mono inline-flex items-center gap-1" title="subnet {canister.subnet || 'lookup…'}">
                                    ⬡ {canister.subnet ? shortId(canister.subnet) : 'subnet'}
                                    <SubnetFlags subnetId={canister.subnet} canisterId={canister.canister_id} />
                                  </span>
                                {/if}
                                {#if governanceConsoleUrl(canister)}
                                  <a
                                    href={governanceConsoleUrl(canister)!}
                                    class="btn-sm btn-secondary text-xs px-2 py-1"
                                  >
                                    {governanceConsoleLabel(canister)}
                                  </a>
                                {/if}
                              </div>
                              <div class="flex items-center gap-2 text-xs">
                                <button
                                  class="font-mono text-primary-600 hover:text-primary-900 transition-colors inline-flex items-center gap-1"
                                  title="Copy canister id"
                                  onclick={() => copy(canister.canister_id)}
                                >
                                  <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3" />
                                  </svg>
                                  {canister.canister_id || '—'}
                                </button>
                                {#if canister.wasm_hash}
                                  <span class="text-primary-400 font-mono" title={canister.wasm_hash}>· {shortHash(canister.wasm_hash)}</span>
                                {/if}
                              </div>
                              {#if canister.controllers?.length}
                                <p class="text-[11px] text-primary-500 font-mono truncate">
                                  controllers:
                                  {#each canister.controllers as principal, i (principal)}
                                    {#if i > 0}<span>, </span>{/if}
                                    {@const label = controllerLabel(principal, principalLabels)}
                                    <span title={label.title}>{label.display}</span>
                                  {/each}
                                </p>
                              {/if}
                            </div>
                            <div class="flex items-center gap-0.5 shrink-0">
                              <!-- Details toggle -->
                              <button class="icon-btn" aria-label="Toggle details" onclick={() => toggleCanister(canister)}>
                                <svg class="w-4 h-4 transition-transform {expandedCanisters[canister.canister_id] ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/>
                                </svg>
                              </button>
                              <!-- Open / Candid UI or governance console -->
                              <a
                                href={canisterLink(canister)}
                                target={governanceConsoleUrl(canister) ? undefined : '_blank'}
                                rel={governanceConsoleUrl(canister) ? undefined : 'noopener noreferrer'}
                                class="icon-btn"
                                aria-label={governanceConsoleUrl(canister) ? 'Open governance console' : canister.kind === 'backend' ? 'Open Candid UI' : 'Open frontend'}
                              >
                                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/>
                                </svg>
                              </a>
                              {#if $isAuthenticated}
                                <!-- Deploy -->
                                <button class="icon-btn" aria-label="Deploy (upgrade WASM)" onclick={() => openUpgradeCanister(canister)}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"/></svg>
                                </button>
                                <!-- Snapshot -->
                                <button class="icon-btn" aria-label="Create snapshot" onclick={() => runCanisterAction('Snapshot', () => createSnapshot(canister.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.776 48.776 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"/></svg>
                                </button>
                                <!-- Revert -->
                                <button class="icon-btn" aria-label="Revert to snapshot" onclick={() => runCanisterAction('Revert', () => revertSnapshot(canister.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3"/></svg>
                                </button>
                                <!-- Stop -->
                                <button class="icon-btn" aria-label="Stop canister" onclick={() => runCanisterAction('Stop', () => stopCanister(canister.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="1" stroke-linecap="round" stroke-linejoin="round"/></svg>
                                </button>
                                <!-- Start -->
                                <button class="icon-btn" aria-label="Start canister" onclick={() => runCanisterAction('Start', () => startCanister(canister.name))}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/></svg>
                                </button>
                                <!-- Rename -->
                                <button class="icon-btn" aria-label="Rename canister" onclick={() => openRenameCanister(canister)}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
                                </button>
                                <!-- Delete (retire to pool) -->
                                <button class="icon-btn text-red-400 hover:text-red-600 hover:bg-red-50" aria-label="Delete canister (return to pool)" onclick={() => openDeleteCanister(canister)}>
                                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
                                </button>
                                <!-- Destroy (permanent) -->
                                <button class="btn-sm bg-red-600 text-white hover:bg-red-700 px-2 py-1 text-xs font-medium rounded-md" title="Permanently destroy canister and reclaim cycles" onclick={() => openDestroyCanister(canister)}>
                                  Destroy
                                </button>
                              {/if}
                            </div>
                          </div>

                          {#if expandedCanisters[canister.canister_id]}
                            {@const cycles = canisterCycles[canister.canister_id]}
                            {@const deploy = canisterDeployment[canister.canister_id]}
                            {@const balance = balanceLabel(cycles)}
                            <div class="mt-3 pt-3 border-t border-[var(--color-border-primary)] space-y-3">
                              <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-primary-500">
                                <span>runtime: <span class="font-medium text-primary-800">{runtimeLabel(cycles?.runtime_status)}</span></span>
                                <span>balance:
                                  <span class="font-medium {balance.low === true ? 'text-amber-700' : balance.low === false ? 'text-emerald-700' : 'text-primary-800'}">
                                    {balance.text}{#if cycles?.cycles !== undefined}<span class="font-mono font-normal text-primary-400"> ({formatCycles(cycles.cycles)})</span>{/if}
                                  </span>
                                </span>
                                <span>last deploy:
                                  {#if deploy?.at}
                                    <span class="font-mono text-primary-800">{formatIsoTs(deploy.at)}</span>
                                    <span class="text-primary-400"> · </span>
                                    <span class="font-medium text-primary-800">{deploy.kind}</span>
                                  {:else}
                                    <span class="font-medium text-primary-800">—</span>
                                  {/if}
                                </span>
                                <span>status: <span class="font-medium text-primary-800">{canister.status || '—'}</span></span>
                                <span>wasm: <span class="font-mono">{canister.wasm_key || '—'}</span></span>
                                {#if canister.wasm_hash}
                                  <span class="font-mono" title={canister.wasm_hash}>hash {shortHash(canister.wasm_hash)}</span>
                                {/if}
                                {#if canister.snapshot_id}
                                  <span class="font-mono" title={canister.snapshot_id}>snapshot {shortHash(canister.snapshot_id)}</span>
                                {/if}
                                <span class="font-mono inline-flex items-center gap-1.5 flex-wrap" title={canister.subnet || 'default (conductor subnet)'}>
                                  subnet {canister.subnet ? shortId(canister.subnet) : '— default'}
                                  <SubnetFlags subnetId={canister.subnet} canisterId={canister.canister_id} />
                                </span>
                              </div>
                              <CanisterControllersBadge
                                canisterId={canister.canister_id}
                                controllers={canister.controllers}
                                {principalLabels}
                                inline
                              />
                              {#if canisterDetailsErr[canister.canister_id]}
                                <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
                                  Couldn't load deployment info: {canisterDetailsErr[canister.canister_id]}
                                </div>
                              {/if}

                              {#if canisterLoading[canister.canister_id]}
                                <div class="skeleton h-4 w-48"></div>
                              {:else}
                                <div>
                                  <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider mb-1.5">Recent events</div>
                                  {#if (canisterEvents[canister.canister_id]?.length ?? 0) === 0}
                                    <div class="text-xs text-primary-400">No events for this canister.</div>
                                  {:else}
                                    <ul class="space-y-1">
                                      {#each canisterEvents[canister.canister_id] as e (e.self_hash || e.idx)}
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
                                    <button class="text-xs text-primary-500 hover:text-primary-800" onclick={() => loadCanisterDetails(canister.canister_id)}>Reload</button>
                                  </div>
                                  {#if canisterLogErr[canister.canister_id]}
                                    <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
                                      Couldn't fetch logs: {canisterLogErr[canister.canister_id]}
                                      {#if $isAuthenticated}
                                        <button class="underline ml-1 font-medium" onclick={() => makeLogsPublic(canister)}>Make logs public</button>
                                      {/if}
                                    </div>
                                  {:else if (canisterLogs[canister.canister_id]?.length ?? 0) === 0}
                                    <div class="text-xs text-primary-400">No logs recorded yet.</div>
                                  {:else}
                                    <pre class="text-[11px] leading-relaxed font-mono bg-primary-900 text-primary-100 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap">{#each canisterLogs[canister.canister_id] as r (r.idx)}<span class="text-primary-400">{fmtTs(r.timestamp_nanos)}</span>  {r.content}
{/each}</pre>
                                  {/if}
                                </div>

                                {#if hasBasiliskFeatures(resolveWasmType(canister))}
                                  <div>
                                    <div class="flex items-center justify-between mb-1.5">
                                      <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider">Inspect (Basilisk)</div>
                                      <button class="text-xs text-primary-500 hover:text-primary-800 disabled:opacity-40" disabled={browseBusy[canister.canister_id]} onclick={() => runBrowse(canister)}>
                                        {browseBusy[canister.canister_id] ? 'Loading…' : 'Browse data'}
                                      </button>
                                    </div>
                                    {#if browseErr[canister.canister_id]}
                                      <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">{browseErr[canister.canister_id]}</div>
                                    {:else if browseData[canister.canister_id] !== undefined}
                                      <pre class="text-[11px] leading-relaxed font-mono bg-primary-50 text-primary-800 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap">{JSON.stringify(browseData[canister.canister_id], null, 2)}</pre>
                                    {:else}
                                      <div class="text-xs text-primary-400">Read-only view of the canister's stable data. Click “Browse data”.</div>
                                    {/if}
                                  </div>

                                  {#if $isAuthenticated}
                                    <div>
                                      <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider mb-1.5">Python console (Basilisk)</div>
                                      <textarea
                                        class="input font-mono text-[11px] w-full min-h-[64px] resize-y"
                                        placeholder={'print(1 + 1)\nimport sys; print(sys.version)'}
                                        bind:value={consoleCode[canister.canister_id]}
                                        onkeydown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runExec(canister); } }}
                                      ></textarea>
                                      <div class="flex items-center justify-between mt-1.5">
                                        <span class="text-[11px] text-primary-400">Runs server-side via <span class="font-mono">__shell__</span>. ⌘/Ctrl+Enter to run.</span>
                                        <button class="btn-secondary btn-sm" disabled={consoleBusy[canister.canister_id]} onclick={() => runExec(canister)}>
                                          {consoleBusy[canister.canister_id] ? 'Running…' : 'Run'}
                                        </button>
                                      </div>
                                      {#if consoleErr[canister.canister_id]}
                                        <div class="text-xs text-red-700 bg-red-50 border border-red-200 rounded-md px-2 py-1.5 mt-1.5">{consoleErr[canister.canister_id]}</div>
                                      {:else if consoleOut[canister.canister_id] !== undefined}
                                        <pre class="text-[11px] leading-relaxed font-mono bg-primary-900 text-primary-100 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap mt-1.5">{consoleOut[canister.canister_id] || '(no output)'}</pre>
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
    danger={modal.danger}
    busy={modalBusy}
    logLines={modalLogLines}
    onsubmit={submitModal}
    oncancel={closeModal}
  />
{/if}

{#if createCanisterStand}
  <CreateCanisterModal
    stand={createCanisterStand}
    catalog={catalog}
    tree={tree}
    families={allFamilies}
    onsuccess={onCreateCanisterSuccess}
    oncancel={closeCreateCanister}
  />
{/if}
