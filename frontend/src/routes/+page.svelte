<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    loadView,
    orchestraExport,
    orchestraEvents,
    wasmList,
    cyclesGet,
    sectionCreate,
    sectionSet,
    sectionDelete,
    standCreate,
    standSet,
    standDelete,
    canisterRegister,
    canisterSet,
    canisterUpgrade,
    canisterSnapshot,
    canisterSnapshots,
    canisterRestore,
    batonProposeRestore,
    canisterStop,
    canisterStart,
    canisterDelete,
    canisterBrowse,
    grantSet,
    getCanisterLogs,
    shortHash,
    shortPrincipal,
    canisterLink,
    formatCycles,
    cyclesIsLow,
    backendCanisterId,
  } from '$lib/api';
  import type {
    OrchestraView, SectionView, StandView, CanisterRow, OrchestraEvent,
    CanisterLogRecord, Wasm, CanisterCycles, ExportDoc, CanisterSnapshots, SnapshotRow,
  } from '$lib/api';
  import { formatBytes } from '$lib/wasmStorePath';
  import { installArgPayload, sheetInstallArgFor } from '$lib/installArg';
  import { batonConsoleUrl, batonIdInStand, governanceConsoleUrl } from '$lib/orchestrationNav';
  import { browserTreeStorage, orchestraOpenPlan, readCachedTree, writeCachedTree } from '$lib/treeCache';
  import { isAuthenticated, isController, principal } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import FormModal from '$lib/components/FormModal.svelte';
  import CreateCanisterModal from '$lib/components/CreateCanisterModal.svelte';
  import DeployBundleModal from '$lib/components/DeployBundleModal.svelte';
  import OrchestraDiagram from '$lib/components/OrchestraDiagram.svelte';
  import OrchestraControlGraph from '$lib/components/OrchestraControlGraph.svelte';
  import CanisterTypeBadges from '$lib/components/CanisterTypeBadges.svelte';
  import SubnetFlags from '$lib/components/SubnetFlags.svelte';
  import CanisterControllersBadge from '$lib/components/CanisterControllersBadge.svelte';
  import { resolveWasmType, canBrowse } from '$lib/canisterTypes';
  import { familyOf, versionOptions } from '$lib/createCanisterForm';
  import { buildPrincipalLabels, controllerLabel } from '$lib/controllerLabels';
  import { sortCanistersForDisplay, isCasalsCanister } from '$lib/orchestraGovernance';
  import { canActOnCanister, canActOnStand } from '$lib/commanderPermissions';
  import { createOrchestrationLogPoll } from '$lib/orchestrationLogPoll';
  import {
    flattenTreeRows,
    listActionState,
    readOrchestraView,
    writeOrchestraView,
    type ListRow,
    type OrchestraLayout,
  } from '$lib/orchestraList';
  import { canEditSection, isCasalsSection } from '$lib/governanceUx';
  import type { Field } from '$lib/components/FormModal.svelte';

  type Values = Record<string, string | boolean>;

  interface ModalConfig {
    title: string;
    description?: string;
    fields: Field[];
    submitLabel: string;
    danger?: boolean;
    onsubmit: (values: Values) => Promise<unknown>;
  }

  let tree = $state<OrchestraView | null>(null);
  let loading = $state(true);
  let error = $state('');
  let catalog = $state<Wasm[]>([]);
  /** The orchestra as an `orchestra_up` document — the source of each canister's install_arg. */
  let exported = $state<ExportDoc | null>(null);
  let bundleTarget = $state<CanisterRow | null>(null);
  const orchestraName = $derived(tree?.orchestra.name ?? '');

  let expandedCanisters = $state<Record<string, boolean>>({});
  let canisterEvents = $state<Record<string, OrchestraEvent[]>>({});
  let canisterLogs = $state<Record<string, CanisterLogRecord[]>>({});
  let canisterLogErr = $state<Record<string, string>>({});
  let canisterLoading = $state<Record<string, boolean>>({});
  let canisterCycles = $state<Record<string, CanisterCycles>>({});
  let canisterDetailsErr = $state<Record<string, string>>({});
  let canisterSnaps = $state<Record<string, CanisterSnapshots>>({});
  let canisterSnapsErr = $state<Record<string, string>>({});

  let browseData = $state<Record<string, unknown>>({});
  let browseErr = $state<Record<string, string>>({});
  let browseBusy = $state<Record<string, boolean>>({});

  let overviewOpen = $state(false);
  let orchestraView = $state<OrchestraLayout>('list');
  function setView(view: OrchestraLayout) {
    orchestraView = view;
    writeOrchestraView(typeof localStorage === 'undefined' ? null : localStorage, view);
  }
  let filterQuery = $state('');

  const principalLabels = $derived(buildPrincipalLabels(tree));

  // A section is kept when any of its stands match, a stand when any of its canisters
  // or the stand itself match; case-insensitive substring match.
  const filteredTree = $derived.by((): OrchestraView | null => {
    if (!tree) return null;
    const q = filterQuery.trim().toLowerCase();
    if (!q) return tree;
    const has = (vals: (string | undefined)[]) => vals.some((v) => (v ?? '').toLowerCase().includes(q));
    const sections = tree.sections
      .map((sec) => {
        const stands = sec.stands
          .map((st) => {
            const canisters = st.canisters.filter((c) =>
              has([c.name, c.canister_id, c.wasm, c.wasm_hash, c.status, c.kind, ...c.tags]));
            return canisters.length || has([st.name, st.description, ...st.grants.map((g) => g.principal)])
              ? { ...st, canisters } : null;
          })
          .filter((st): st is StandView => !!st);
        return stands.length || has([sec.name, sec.description, ...sec.grants.map((g) => g.principal)])
          ? { ...sec, stands } : null;
      })
      .filter((sec): sec is SectionView => !!sec);
    return { ...tree, sections };
  });

  // ── List view: flat rows + selection + toolbar ─────────────────────────────
  const listRows = $derived(flattenTreeRows(filteredTree, { sort: sortCanistersForDisplay, isCore: isCasalsSection }));
  const selectableIds = $derived(new Set(listRows.filter((r) => r.canister.canister_id).map((r) => r.canister.canister_id)));

  let selectedIds = $state<Set<string>>(new Set());
  const selectedRows = $derived(listRows.filter((r) => r.canister.canister_id && selectedIds.has(r.canister.canister_id)));
  const allVisibleSelected = $derived(selectableIds.size > 0 && [...selectableIds].every((id) => selectedIds.has(id)));
  const someVisibleSelected = $derived(!allVisibleSelected && [...selectableIds].some((id) => selectedIds.has(id)));

  function toggleSelected(id: string) {
    if (!id) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    selectedIds = next;
  }
  function toggleSelectAllVisible() {
    const next = new Set(selectedIds);
    if (allVisibleSelected) for (const id of selectableIds) next.delete(id);
    else for (const id of selectableIds) next.add(id);
    selectedIds = next;
  }
  function clearSelection() {
    selectedIds = new Set();
  }

  const actions = $derived(listActionState(selectedRows, {
    isController: $isController === true,
    allows: (row: ListRow, key: string) =>
      !!tree && !!$principal.trim() && canActOnStand(tree, row.section, row.stand, $principal, key),
  }));
  const single = $derived(selectedRows.length === 1 ? selectedRows[0] : null);

  function canEditArrangement(section: SectionView): boolean {
    return !!tree && canEditSection($principal, tree.grants, section.grants, $isController === true);
  }

  function singleControllerLabel(c: CanisterRow): string {
    return controllerLabel(c.controllers[0], principalLabels).display;
  }
  /** A friendly alias for the canister id when one is set and differs from its name. */
  function canisterAlias(c: CanisterRow): string {
    const alias = tree?.orchestra.aliases[c.canister_id];
    return alias && alias !== c.name ? alias : '';
  }

  // Sequential batch over the selection; one toast per outcome, one reload at the end.
  async function runBatch(label: string, rows: ListRow[], fn: (c: CanisterRow) => Promise<unknown>) {
    if (!rows.length) return;
    let ok = 0;
    const failed: string[] = [];
    for (const r of rows) {
      try {
        await fn(r.canister);
        ok += 1;
      } catch (e: any) {
        failed.push(`${r.canister.name}: ${e?.message ?? 'failed'}`);
      }
    }
    if (ok) toasts.success(`${label}: ${ok} canister${ok === 1 ? '' : 's'}`);
    for (const f of failed) toasts.error(`${label} failed — ${f}`);
    await load();
  }

  function openDeleteSelected() {
    if (selectedRows.length === 1) return openDeleteCanister(selectedRows[0].canister);
    const rows = selectedRows;
    openModal({
      title: `Delete ${rows.length} canisters`,
      description: `${rows.map((r) => r.canister.name).join(', ')} — Casals forgets them; the canisters stay on the IC.`,
      fields: [{ name: 'confirm', label: 'Type "DELETE" to confirm', required: true }],
      submitLabel: `Delete ${rows.length} canisters`,
      danger: true,
      onsubmit: async (v) => {
        if (String(v.confirm).trim() !== 'DELETE') throw new Error('Confirmation does not match');
        const failed: string[] = [];
        for (const r of rows) {
          try { await canisterDelete(r.canister.name); }
          catch (e: any) { failed.push(`${r.canister.name}: ${e?.message ?? 'failed'}`); }
        }
        clearSelection();
        if (failed.length) throw new Error(failed.join('; '));
      },
    });
  }

  let modal = $state<ModalConfig | null>(null);
  let modalBusy = $state(false);
  let modalLogLines = $state<string[]>([]);
  let createCanisterStand = $state<StandView | null>(null);
  const logPoll = createOrchestrationLogPoll((lines) => (modalLogLines = lines));

  async function load(opts: { background?: boolean } = {}) {
    if (!opts.background) {
      loading = true;
      error = '';
    }
    try {
      [tree, catalog, exported] = await Promise.all([
        loadView(),
        wasmList().catch(() => [] as Wasm[]),
        orchestraExport().catch(() => null),
      ]);
      writeCachedTree(backendCanisterId(), tree, browserTreeStorage());
      void loadCycles();
    } catch (e: any) {
      if (!opts.background || !tree) error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  async function loadCycles() {
    const c = await cyclesGet().catch(() => null);
    if (c) canisterCycles = Object.fromEntries(c.canisters.map((row) => [row.canister_id, row]));
  }

  const allFamilies = $derived([...new Set(catalog.map((w) => familyOf(w.key)).filter(Boolean))].sort());

  function shortId(id: string): string {
    if (!id) return '';
    return id.length > 13 ? `${id.slice(0, 5)}…${id.slice(-5)}` : id;
  }

  function standFamilies(stand: StandView): string[] {
    return [...new Set(stand.canisters.map((c) => familyOf(c.wasm)).filter(Boolean))];
  }

  onMount(() => {
    orchestraView = readOrchestraView(typeof localStorage === 'undefined' ? null : localStorage);
    const plan = orchestraOpenPlan(readCachedTree(backendCanisterId(), browserTreeStorage()));
    if (plan.tree && 'orchestra' in plan.tree) {
      tree = plan.tree as unknown as OrchestraView;
      loading = false;
      void load({ background: true });
    } else {
      void load();
    }
  });

  function balanceLabel(row: CanisterCycles | undefined): { text: string; low: boolean | null } {
    const low = row ? cyclesIsLow(row.cycles, row.min) : null;
    if (low === false) return { text: 'ok', low: false };
    if (low === true) return { text: 'low balance', low: true };
    return { text: '—', low: null };
  }

  async function toggleCanister(canister: CanisterRow) {
    const cid = canister.canister_id;
    if (!cid) return;
    const open = !expandedCanisters[cid];
    expandedCanisters[cid] = open;
    if (open) await loadCanisterDetails(cid, canister.name);
  }

  async function loadSnapshots(cid: string, name: string) {
    canisterSnapsErr[cid] = '';
    try {
      canisterSnaps[cid] = await canisterSnapshots(name);
    } catch (e: any) {
      canisterSnapsErr[cid] = e?.message ?? String(e);
    }
  }

  async function loadCanisterDetails(cid: string, name: string) {
    canisterLoading[cid] = true;
    canisterLogErr[cid] = '';
    canisterDetailsErr[cid] = '';
    try {
      const [evs, logs] = await Promise.all([
        orchestraEvents({ canister_id: cid, take: 8 }),
        getCanisterLogs(cid).catch((e: any) => {
          canisterLogErr[cid] = e?.message ?? String(e);
          return [] as CanisterLogRecord[];
        }),
        $isAuthenticated ? loadSnapshots(cid, name) : Promise.resolve(),
      ]);
      canisterEvents[cid] = evs;
      canisterLogs[cid] = logs;
    } catch (e: any) {
      canisterDetailsErr[cid] = e?.message ?? String(e);
    } finally {
      canisterLoading[cid] = false;
    }
  }

  function canRevert(canister: CanisterRow, via: CanisterSnapshots['via']): boolean {
    if ($isController === true) return true;
    return canActOnCanister(tree, $principal, canister, via === 'baton' ? 'canister.deploy' : 'canister.lifecycle');
  }

  function snapTime(ns: number): string {
    return new Date(Math.floor(ns / 1e6)).toLocaleString();
  }

  function openRevert(canister: CanisterRow, listing: CanisterSnapshots, snap: SnapshotRow) {
    const names = snap.restores.map((r) => r.canister).join(', ');
    const when = snapTime(snap.taken_at);
    if (listing.via === 'conductor') {
      openModal({
        title: `Revert ${canister.name}`,
        description: `Load the snapshot from ${when}${snap.label ? ` (${snap.label})` : ''}. Everything the canister stored since is lost.`,
        fields: [],
        submitLabel: 'Revert',
        danger: true,
        onsubmit: async () => {
          await canisterRestore(canister.name, snap.id);
          await loadSnapshots(canister.canister_id, canister.name);
        },
      });
      return;
    }
    openModal({
      title: `Revert ${snap.restores.length > 1 ? 'the stand' : canister.name}`,
      description:
        `Propose to baton ${listing.baton} putting ${names} back on the snapshot${snap.restores.length > 1 ? 's' : ''} from ${when}` +
        `${snap.label ? ` (${snap.label})` : ''}. Once approved, the baton stops them, snapshots them again, loads these and starts them. ` +
        'Everything they stored since is lost.',
      fields: [],
      submitLabel: 'Propose revert',
      danger: true,
      onsubmit: async () => {
        const filed = await batonProposeRestore(
          canister.stand,
          snap.restores.map((r) => ({ canister: r.canister, snapshot_id: r.snapshot_id })),
          `reverting to ${when}`,
        );
        void goto(batonConsoleUrl(filed.baton_id));
      },
    });
  }

  async function runBrowse(canister: CanisterRow, query?: Record<string, unknown>) {
    const cid = canister.canister_id;
    browseBusy[cid] = true;
    browseErr[cid] = '';
    try {
      browseData[cid] = await canisterBrowse(canister.name, query);
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      browseErr[cid] = /__browse__|method|not found|no query method/i.test(msg)
        ? 'This canister does not expose introspection (rebuild with __basilisk_features__).'
        : msg;
    } finally {
      browseBusy[cid] = false;
    }
  }

  function fmtTs(nanos: bigint): string {
    try {
      return new Date(Number(nanos / 1_000_000n)).toLocaleTimeString();
    } catch {
      return '';
    }
  }

  function evBadge(type: string): string {
    if (type.includes('failed')) return 'bg-red-50 text-red-700 border border-red-200';
    if (type === 'canister_restore') return 'bg-amber-50 text-amber-700 border border-amber-200';
    return 'badge-neutral';
  }

  function evSummary(e: OrchestraEvent): string {
    const p = e.payload ?? {};
    if (p.wasm) return `→ ${p.wasm}`;
    if (p.error) return String(p.error);
    const rest = Object.fromEntries(Object.entries(p).filter(([k]) => k !== 'canister'));
    return Object.keys(rest).length ? JSON.stringify(rest).slice(0, 80) : '';
  }

  async function copy(text: string) {
    if (await copyText(text)) toasts.info('Copied to clipboard');
    else toasts.error('Could not copy');
  }

  function governanceConsoleLabel(canister: CanisterRow): string {
    const t = resolveWasmType(canister);
    if (t === 'baton') return 'Baton';
    if (t === 'multisig') return 'Multisig';
    return 'Console';
  }

  const text = (v: string | boolean | undefined) => String(v ?? '').trim();

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
    logPoll.start();
    try {
      await modal.onsubmit(values);
      toasts.success(`${modal.title} succeeded`);
      modal = null;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Operation failed');
    } finally {
      logPoll.stop();
      modalBusy = false;
    }
  }

  // ── Modal openers ──

  function openCreateSection() {
    openModal({
      title: 'Create section',
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'Production' },
        { name: 'description', label: 'Description', type: 'textarea' },
      ],
      submitLabel: 'Create section',
      onsubmit: (v) => sectionCreate({ name: text(v.name), description: text(v.description) }),
    });
  }

  function openCreateStand(section: SectionView) {
    openModal({
      title: 'Create stand',
      description: `In section "${section.name}"`,
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'my-app' },
        { name: 'description', label: 'Description', type: 'textarea' },
      ],
      submitLabel: 'Create stand',
      onsubmit: (v) => standCreate({ section: section.name, name: text(v.name), description: text(v.description) }),
    });
  }

  function openRegisterCanister(stand: StandView) {
    openModal({
      title: 'Register canister',
      description: `In stand "${stand.name}" — registers an existing canister`,
      fields: [
        { name: 'name', label: 'Name', required: true, placeholder: 'app-frontend' },
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
      onsubmit: (v) => canisterRegister({
        stand: stand.name, name: text(v.name), canister_id: text(v.canister_id),
        kind: text(v.kind) === 'frontend' ? 'frontend' : 'backend',
      }),
    });
  }

  // ── Rename / Delete ───────────────────────────────────────────────────────
  function openRenameSection(section: SectionView) {
    openModal({
      title: `Rename section`,
      fields: [
        { name: 'name', label: 'New name', required: true, value: section.name },
        { name: 'description', label: 'Description', value: section.description },
      ],
      submitLabel: 'Rename',
      onsubmit: (v) => sectionSet({ section: section.name, name: text(v.name), description: text(v.description) }),
    });
  }

  function openRenameStand(stand: StandView) {
    openModal({
      title: `Rename stand`,
      fields: [
        { name: 'name', label: 'New name', required: true, value: stand.name },
        { name: 'description', label: 'Description', value: stand.description },
      ],
      submitLabel: 'Rename',
      onsubmit: (v) => standSet({ stand: stand.name, name: text(v.name), description: text(v.description) }),
    });
  }

  function openRenameCanister(canister: CanisterRow) {
    openModal({
      title: `Rename canister`,
      fields: [{ name: 'name', label: 'New name', required: true, value: canister.name }],
      submitLabel: 'Rename',
      onsubmit: (v) => canisterSet({ canister: canister.name, name: text(v.name) }),
    });
  }

  function openEditCanisterTags(canister: CanisterRow) {
    openModal({
      title: `Tags for "${canister.name}"`,
      description: 'Comma-separated labels. WASM type badges are set automatically.',
      fields: [{ name: 'tags', label: 'Tags', value: canister.tags.join(', '), placeholder: 'staging, team-alpha' }],
      submitLabel: 'Save tags',
      onsubmit: (v) => canisterSet({ canister: canister.name, tags: text(v.tags).split(',').map((t) => t.trim()).filter(Boolean) }),
    });
  }

  function confirmDelete(kind: string, name: string, description: string, run: () => Promise<unknown>) {
    openModal({
      title: `Delete ${kind} "${name}"`,
      description,
      fields: [{ name: 'confirm', label: `Type "${name}" to confirm`, required: true }],
      submitLabel: `Delete ${kind}`,
      danger: true,
      onsubmit: async (v) => {
        if (text(v.confirm) !== name) throw new Error('Name does not match');
        return run();
      },
    });
  }

  const openDeleteSection = (section: SectionView) =>
    confirmDelete('section', section.name, 'Only an empty section can be deleted.', () => sectionDelete(section.name));
  const openDeleteStand = (stand: StandView) =>
    confirmDelete('stand', stand.name, 'Casals forgets the stand and its canisters; the canisters stay on the IC.', () => standDelete(stand.name));
  const openDeleteCanister = (canister: CanisterRow) =>
    confirmDelete('canister', canister.name, 'Casals forgets the canister; it stays on the IC.', () => canisterDelete(canister.name));

  function openAddCommander(scope: string, label: string) {
    openModal({
      title: 'Add commander',
      description: `Grant full access on ${label} (existing commanders are kept). Narrow it on the Operator access page.`,
      fields: [{ name: 'principal', label: 'Commander principal', required: true, placeholder: 'aaaaa-aa' }],
      submitLabel: 'Add commander',
      onsubmit: (v) => grantSet({ scope, principal: text(v.principal), permissions: ['*'] }),
    });
  }

  async function onCreateCanisterSuccess() {
    createCanisterStand = null;
    toasts.success('Canister created');
    await load();
  }

  function wasmField(family: string, fallback = ''): Field {
    const opts = versionOptions(family, catalog);
    if (opts.length) return { name: 'wasm', label: 'Version', type: 'select', value: family, options: opts };
    const families = allFamilies.map((f) => ({ value: f, label: f }));
    if (families.length) return { name: 'wasm', label: 'WASM family', type: 'select', value: family || families[0].value, options: families };
    return { name: 'wasm', label: 'WASM key', required: true, value: fallback, placeholder: 'hello-world-basilisk' };
  }

  function openUpgradeStand(stand: StandView) {
    const batonId = batonIdInStand(stand);
    if (batonId) return goto(batonConsoleUrl(batonId));
    const fams = standFamilies(stand);
    openModal({
      title: 'Deploy stand',
      description: `Deploy all canisters in stand "${stand.name}"`,
      fields: [
        wasmField(fams.length === 1 ? fams[0] : ''),
        {
          name: 'reinstall',
          type: 'checkbox',
          label: 'Reinstall (wipes all canister state — data will be lost)',
          value: false,
          help: 'Reinstall erases canister state and runs init again with each canister\'s install_arg.',
        },
      ],
      submitLabel: 'Deploy',
      onsubmit: (v) => canisterUpgrade({ stand: stand.name, wasm: text(v.wasm), reinstall: Boolean(v.reinstall) }),
    });
  }

  function openUpgradeCanister(canister: CanisterRow) {
    const home = tree?.sections.flatMap((sec) => sec.stands).find((st) => st.name === canister.stand);
    const batonId = batonIdInStand(home);
    if (batonId) return goto(batonConsoleUrl(batonId));
    const family = familyOf(canister.wasm);
    openModal({
      title: 'Deploy canister',
      description: `Deploy canister "${canister.name}"${family ? ` · family ${family}` : ''}`,
      fields: [
        wasmField(family, canister.wasm),
        {
          name: 'reinstall',
          type: 'checkbox',
          label: 'Reinstall (wipes canister state — data will be lost)',
          value: false,
          help: 'Reinstall erases canister state and runs init again with the argument below.',
        },
        {
          name: 'install_arg',
          type: 'textarea',
          label: 'Init argument',
          value: sheetInstallArgFor(exported, canister.name),
          rows: 4,
          showWhen: 'reinstall',
          help: 'The current Candid argument. Edit it to pass a different one; blank keeps it.',
        },
      ],
      submitLabel: 'Deploy',
      onsubmit: async (v) => {
        const reinstall = Boolean(v.reinstall);
        const arg = reinstall ? installArgPayload(String(v.install_arg ?? '')) : undefined;
        if (arg !== undefined && arg !== sheetInstallArgFor(exported, canister.name)) {
          await canisterSet({ canister: canister.name, install_arg: arg });
        }
        return canisterUpgrade({ canister: canister.name, wasm: text(v.wasm), reinstall });
      },
    });
  }
</script>

<svelte:head><title>Casals · Orchestra</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <!-- Header -->
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Orchestra</h1>
      {#if orchestraName}
        <p class="text-base font-medium text-primary-700 mt-0.5">{orchestraName}</p>
      {/if}
      <p class="text-sm text-primary-500 mt-1">Orchestra → Section → Stand → Canister lifecycle orchestration</p>
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
      <button class="btn-secondary btn-sm" onclick={() => void load()}>
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
            class="px-2.5 py-2 text-xs font-medium inline-flex items-center gap-1 {orchestraView === 'list' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
            aria-pressed={orchestraView === 'list'}
            onclick={() => setView('list')}
          >
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM3.75 12h.007v.008H3.75V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm-.375 5.25h.007v.008H3.75v-.008zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
            </svg>
            List
          </button>
          <button
            type="button"
            class="px-2.5 py-2 text-xs font-medium inline-flex items-center gap-1 {orchestraView === 'diagram' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
            aria-pressed={orchestraView === 'diagram'}
            onclick={() => setView('diagram')}
          >
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 016 0h12a2.25 2.25 0 012.25 2.25v12A2.25 2.25 0 0118 18H6a2.25 2.25 0 01-2.25-2.25V6zM8.25 6.75v10.5M15.75 6.75v10.5" />
            </svg>
            Topology
          </button>
          <button
            type="button"
            class="px-2.5 py-2 text-xs font-medium inline-flex items-center gap-1 {orchestraView === 'control' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
            aria-pressed={orchestraView === 'control'}
            onclick={() => setView('control')}
          >
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
            </svg>
            Control
          </button>
        </div>
        <!-- Divider + Overview toggle -->
        {#if tree}
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

      <!-- List actions: act on the selected canisters; enabled by permission × state -->
      {#if orchestraView === 'list' && $isAuthenticated}
        <div class="border-t border-primary-100 px-3 py-2 flex flex-wrap items-center gap-1.5">
          <span class="text-xs text-primary-500 mr-1 min-w-[6.5rem]">
            {#if selectedRows.length}
              {selectedRows.length} selected
              <button class="underline hover:text-primary-800 ml-1" onclick={clearSelection}>clear</button>
            {:else}
              Select canisters
            {/if}
          </span>
          {#if single}
            <a
              href={canisterLink(single.canister)}
              target={governanceConsoleUrl(single.canister) ? undefined : '_blank'}
              rel={governanceConsoleUrl(single.canister) ? undefined : 'noopener noreferrer'}
              class="btn-secondary btn-sm"
              title={governanceConsoleUrl(single.canister) ? 'Open governance console' : single.canister.kind === 'backend' ? 'Open Candid UI' : 'Open frontend'}
            >
              <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>
              Open
            </a>
          {/if}
          <button class="btn-secondary btn-sm" disabled={!actions.deploy} title={selectedRows.length > 1 ? 'Deploy acts on one canister at a time' : 'Deploy (upgrade WASM)'} onclick={() => single && openUpgradeCanister(single.canister)}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"/></svg>
            Deploy
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.bundle} title={selectedRows.length > 1 ? 'Deploy frontend bundle acts on one canister at a time' : single && !actions.bundle && actions.deploy ? 'Only a frontend (asset canister) serves a bundle' : 'Deploy frontend bundle (ship the store\'s bundle to this frontend)'} onclick={() => single && (bundleTarget = single.canister)}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/></svg>
            Deploy frontend bundle
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.snapshot} title="Create snapshot" onclick={() => runBatch('Snapshot', selectedRows, (c) => canisterSnapshot(c.name))}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.776 48.776 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"/></svg>
            Snapshot
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.revert} title={selectedRows.length && !actions.revert ? 'Every selected canister needs a snapshot taken through Casals' : 'Revert to snapshot'} onclick={() => runBatch('Revert', selectedRows, (c) => canisterRestore(c.name, c.snapshot))}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3"/></svg>
            Revert
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.stop} title="Stop canister" onclick={() => runBatch('Stop', selectedRows, (c) => canisterStop(c.name))}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="1" stroke-linecap="round" stroke-linejoin="round"/></svg>
            Stop
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.start} title="Start canister" onclick={() => runBatch('Start', selectedRows, (c) => canisterStart(c.name))}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/></svg>
            Start
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.rename} title={single?.core ? 'Casals core canisters are named by the sheet' : 'Rename canister'} onclick={() => single && openRenameCanister(single.canister)}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487a2.25 2.25 0 1 1 3.182 3.182L7.5 21H3v-4.5L16.862 4.487z"/></svg>
            Rename
          </button>
          <button class="btn-secondary btn-sm" disabled={!actions.tags} title="Edit tags" onclick={() => single && openEditCanisterTags(single.canister)}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9.568 3H5.25A2.25 2.25 0 0 0 3 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 0 0 5.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 0 0 9.568 3Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M6 6h.008v.008H6V6Z"/></svg>
            Tags
          </button>
          <button class="btn-secondary btn-sm text-red-600 hover:bg-red-50 disabled:text-primary-400" disabled={!actions.delete} title={selectedRows.some((r) => r.core) ? 'Casals core canisters cannot be deleted here' : 'Delete (Casals forgets it; it stays on the IC)'} onclick={openDeleteSelected}>
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
            Delete
          </button>
        </div>
      {/if}

      <!-- Overview detail grid — expands inline, no separate card -->
      {#if tree && overviewOpen}
        <div class="border-t border-primary-100 px-4 py-3 grid grid-cols-3 sm:grid-cols-6 gap-x-6 gap-y-3 text-sm">
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Sections</span>
            <span class="font-semibold text-primary-900">{tree.sections.length}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Stands</span>
            <span class="font-semibold text-primary-900">{tree.orchestra.stands.length}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Canisters</span>
            <span class="font-semibold text-primary-900">{tree.orchestra.canisters.length}</span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">WASMs</span>
            <span class="font-semibold text-primary-900">{catalog.length}</span>
          </div>
          <div class="flex flex-col gap-0.5 sm:col-span-2">
            <span class="text-[10px] font-semibold text-primary-400 uppercase tracking-wider">Conductor hosting</span>
            <span class="inline-flex items-center gap-2 text-sm text-primary-700">
              <span class="font-mono text-xs text-primary-500 group/subnet relative">{shortId(backendCanisterId())}
                <SubnetFlags canisterId={backendCanisterId()} size="md" hoverOnly />
              </span>
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
        <OrchestraDiagram
          tree={filteredTree}
          {orchestraName}
          canManage={$isAuthenticated}
          onCreateStand={openCreateStand}
          onAddSectionCommander={(section) => openAddCommander(`section:${section.name}`, `section "${section.name}"`)}
          onRenameSection={openRenameSection}
          onDeleteSection={openDeleteSection}
          onCreateCanister={(stand) => (createCanisterStand = stand)}
          onRegisterCanister={openRegisterCanister}
          onUpgradeStand={openUpgradeStand}
          onAddStandCommander={(stand) => openAddCommander(`stand:${stand.name}`, `stand "${stand.name}"`)}
          onRenameStand={openRenameStand}
          onDeleteStand={openDeleteStand}
          canEditArrangement={canEditArrangement}
        />
      </div>
    {:else if orchestraView === 'control'}
      <div class="card p-5">
        <OrchestraControlGraph
          tree={filteredTree}
          casalsBackendId={backendCanisterId()}
          principalLabel={(p) => principalLabels.get(p) ?? shortPrincipal(p)}
          onRefreshControllers={() => load()}
          controllersRefreshing={loading}
        />
      </div>
    {/if}
    {#if orchestraView === 'list'}
    <div class="space-y-4">
      <div class="card overflow-hidden">
        {#if listRows.length === 0}
          <div class="text-center py-10 text-primary-400 text-sm">No canisters yet.</div>
        {:else}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs uppercase tracking-wide text-primary-500 border-b border-primary-100 bg-primary-50/60">
                <th class="pl-4 pr-2 py-2.5 w-8">
                  {#if $isAuthenticated}
                    <input
                      type="checkbox"
                      class="w-4 h-4 rounded border-primary-300 accent-primary-800 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="Select all visible canisters"
                      checked={allVisibleSelected}
                      indeterminate={someVisibleSelected}
                      disabled={selectableIds.size === 0}
                      onchange={toggleSelectAllVisible}
                    />
                  {/if}
                </th>
                <th class="px-2 py-2.5 font-medium">Canister</th>
                <th class="px-2 py-2.5 font-medium hidden md:table-cell">Section</th>
                <th class="px-2 py-2.5 font-medium hidden md:table-cell">Stand</th>
                <th class="px-2 py-2.5 font-medium hidden lg:table-cell">Principal</th>
                <th class="px-2 py-2.5 font-medium">Controllers</th>
                <th class="px-2 py-2.5 font-medium hidden sm:table-cell">Tags</th>
                <th class="pl-2 pr-3 py-2.5 w-20"></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-primary-50">
              {#each listRows as row (row.key)}
                {@const canister = row.canister}
                {@const cid = canister.canister_id}
                {@const selected = !!cid && selectedIds.has(cid)}
                {@const alias = canisterAlias(canister)}
                <tr
                  class="align-top hover:bg-primary-50/40 {selected ? 'bg-primary-50/70' : ''}
                    {isCasalsCanister(canister) ? 'bg-primary-50/30' : ''}
                    {resolveWasmType(canister) === 'multisig' ? 'bg-emerald-50/30' : ''}
                    {resolveWasmType(canister) === 'baton' ? 'bg-orange-50/30' : ''}"
                >
                  <td class="pl-4 pr-2 py-2.5">
                    {#if $isAuthenticated}
                      <input
                        type="checkbox"
                        class="w-4 h-4 rounded border-primary-300 accent-primary-800 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label="Select {canister.name}"
                        checked={selected}
                        disabled={!cid}
                        onchange={() => toggleSelected(cid)}
                      />
                    {/if}
                  </td>
                  <td class="px-2 py-2.5 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="font-medium text-primary-900">{canister.name}</span>
                      <span class="badge {canister.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">{canister.kind}</span>
                      {#if canister.status && canister.status !== 'ready'}
                        <span class="badge badge-neutral">{canister.status}</span>
                      {/if}
                      {#if governanceConsoleUrl(canister)}
                        <a href={governanceConsoleUrl(canister)!} class="text-xs text-primary-600 underline decoration-dotted hover:text-primary-900">{governanceConsoleLabel(canister)}</a>
                      {/if}
                    </div>
                    {#if alias}
                      <div class="text-xs text-primary-500 mt-0.5" title="Alias for {cid}">{alias}</div>
                    {/if}
                    <div class="md:hidden text-xs text-primary-400 mt-0.5">{row.section.name} / {row.stand.name}</div>
                  </td>
                  <td class="px-2 py-2.5 text-primary-700 hidden md:table-cell whitespace-nowrap">
                    {#if row.core}<span class="badge bg-primary-800 text-white border border-primary-800 mr-1">casals-core</span>{/if}{row.core ? (orchestraName || row.section.name) : row.section.name}
                    {#if canEditArrangement(row.section)}
                      <a class="ml-2 text-xs text-primary-600 underline decoration-dotted hover:text-primary-900" href={`/section/${encodeURIComponent(row.section.name)}/arrangements`}>Edit</a>
                    {/if}
                  </td>
                  <td class="px-2 py-2.5 text-primary-700 hidden md:table-cell whitespace-nowrap">{row.stand.name}</td>
                  <td class="px-2 py-2.5 hidden lg:table-cell">
                    {#if cid}
                      <button
                        class="text-xs font-mono text-primary-600 hover:text-primary-900 transition-colors inline-flex items-center gap-1"
                        title="Copy canister id"
                        onclick={() => copy(cid)}
                      >
                        <svg class="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m11.25 2.625v-3.375a1.125 1.125 0 00-1.125-1.125H15.75m4.5 0H18a1.125 1.125 0 01-1.125-1.125V3"/></svg>
                        {cid}
                      </button>
                    {:else}
                      <span class="text-xs text-primary-400">not provisioned</span>
                    {/if}
                  </td>
                  <td class="px-2 py-2.5 whitespace-nowrap">
                    {#if canister.controllers.length === 1}
                      <span class="text-xs text-primary-600" title={canister.controllers[0]}>{singleControllerLabel(canister)}</span>
                    {:else if canister.controllers.length > 1}
                      <CanisterControllersBadge canisterId={cid} controllers={canister.controllers} {principalLabels} />
                    {:else}
                      <span class="text-xs text-primary-400">—</span>
                    {/if}
                  </td>
                  <td class="px-2 py-2.5 hidden sm:table-cell">
                    <div class="flex items-center gap-1 flex-wrap">
                      <CanisterTypeBadges {canister} />
                      {#if cid}
                        <SubnetFlags canisterId={cid} variant="badge" />
                      {/if}
                    </div>
                  </td>
                  <td class="pl-2 pr-3 py-2">
                    <div class="flex items-center justify-end gap-0.5">
                      <a
                        href={canisterLink(canister)}
                        target={governanceConsoleUrl(canister) ? undefined : '_blank'}
                        rel={governanceConsoleUrl(canister) ? undefined : 'noopener noreferrer'}
                        class="icon-btn"
                        aria-label={governanceConsoleUrl(canister) ? 'Open governance console' : canister.kind === 'backend' ? 'Open Candid UI' : 'Open frontend'}
                      >
                        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>
                      </a>
                      <button class="icon-btn" aria-label={expandedCanisters[cid] ? 'Hide details' : 'Show details'} disabled={!cid} onclick={() => toggleCanister(canister)}>
                        <svg class="w-4 h-4 transition-transform {expandedCanisters[row.canister.canister_id] ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
                      </button>
                    </div>
                  </td>
                </tr>
                {#if cid && expandedCanisters[cid]}
                  <tr class="bg-primary-50/20">
                    <td></td>
                    <td colspan="7" class="px-2 pb-4 pt-1">
                      {@render canisterDetails(canister)}
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
        </div>
        {/if}
      </div>
    </div>
    {/if}
  {/if}
</div>

{#snippet canisterDetails(canister: CanisterRow)}
    {@const cycles = canisterCycles[canister.canister_id]}
    {@const balance = balanceLabel(cycles)}
    <div class="space-y-3">
      <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-primary-500">
        <span>balance:
          <span class="font-medium {balance.low === true ? 'text-amber-700' : balance.low === false ? 'text-emerald-700' : 'text-primary-800'}">
            {balance.text}{#if cycles?.cycles !== undefined}<span class="font-mono font-normal text-primary-400"> ({formatCycles(cycles.cycles)})</span>{/if}
          </span>
        </span>
        <span>status: <span class="font-medium text-primary-800">{canister.status || '—'}</span></span>
        <span>wasm: <span class="font-mono">{canister.wasm || '—'}</span></span>
        {#if canister.wasm_hash}
          <span class="font-mono" title={canister.wasm_hash}>hash {shortHash(canister.wasm_hash)}</span>
        {/if}
        {#if canister.snapshot}
          <span class="font-mono" title={canister.snapshot}>snapshot {shortHash(canister.snapshot)}</span>
        {/if}
        <SubnetFlags
          canisterId={canister.canister_id}
          variant="inline"
        />
      </div>
      <CanisterControllersBadge
        canisterId={canister.canister_id}
        controllers={canister.controllers}
        {principalLabels}
        inline
      />
      {#if canisterDetailsErr[canister.canister_id]}
        <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
          Couldn't load details: {canisterDetailsErr[canister.canister_id]}
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
              {#each canisterEvents[canister.canister_id] as e (e.idx)}
                <li class="text-xs flex items-start gap-2">
                  <span class="badge {evBadge(e.type)} shrink-0">{e.type}</span>
                  <span class="text-primary-600 break-words min-w-0">{evSummary(e)}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        {#if $isAuthenticated}
          {@const listing = canisterSnaps[canister.canister_id]}
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider">
                Snapshots{#if listing}<span class="normal-case font-normal tracking-normal"> · keeps the newest {listing.keep}{#if listing.via === 'baton'}, through baton {listing.baton}{/if}</span>{/if}
              </div>
            </div>
            {#if canisterSnapsErr[canister.canister_id]}
              <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
                Couldn't list snapshots: {canisterSnapsErr[canister.canister_id]}
              </div>
            {:else if !listing || listing.snapshots.length === 0}
              <div class="text-xs text-primary-400">No snapshots.</div>
            {:else}
              <ul class="divide-y divide-[var(--color-border-primary)] text-xs">
                {#each listing.snapshots as s (s.id)}
                  <li class="flex items-center gap-3 py-1.5">
                    <span class="text-primary-800 shrink-0">{snapTime(s.taken_at)}</span>
                    <span class="text-primary-600 min-w-0 truncate" title={s.label}>{s.label || '—'}</span>
                    {#if s.restores.length > 1}
                      <span class="text-primary-400 shrink-0" title={s.restores.map((r) => r.canister).join(', ')}>with {s.restores.length - 1} other{s.restores.length === 2 ? '' : 's'}</span>
                    {/if}
                    <span class="font-mono text-primary-400 shrink-0" title={s.id}>{shortHash(s.id)}</span>
                    <span class="text-primary-400 shrink-0 ml-auto">{formatBytes(s.size)}</span>
                    {#if canRevert(canister, listing.via)}
                      <button class="text-xs text-primary-500 hover:text-primary-800 shrink-0" onclick={() => openRevert(canister, listing, s)}>Revert</button>
                    {/if}
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/if}

        <div>
          <div class="flex items-center justify-between mb-1.5">
            <div class="text-xs font-semibold text-primary-400 uppercase tracking-wider">Canister logs</div>
            <button class="text-xs text-primary-500 hover:text-primary-800" onclick={() => loadCanisterDetails(canister.canister_id, canister.name)}>Reload</button>
          </div>
          {#if canisterLogErr[canister.canister_id]}
            <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
              Couldn't fetch logs: {canisterLogErr[canister.canister_id]}
            </div>
          {:else if (canisterLogs[canister.canister_id]?.length ?? 0) === 0}
            <div class="text-xs text-primary-400">No logs recorded yet.</div>
          {:else}
            <pre class="text-[11px] leading-relaxed font-mono bg-primary-900 text-primary-100 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap">{#each canisterLogs[canister.canister_id] as r (r.idx)}<span class="text-primary-400">{fmtTs(r.timestamp_nanos)}</span>  {r.content}
{/each}</pre>
          {/if}
        </div>

        {#if canBrowse(canister) && $isAuthenticated}
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
        {/if}

      {/if}
    </div>
{/snippet}

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

{#if bundleTarget}
  <DeployBundleModal
    canister={bundleTarget}
    canisters={tree?.orchestra.canisters ?? []}
    ondone={async () => { bundleTarget = null; await load(); }}
    oncancel={() => (bundleTarget = null)}
  />
{/if}

{#if createCanisterStand}
  <CreateCanisterModal
    stand={createCanisterStand}
    catalog={catalog}
    tree={tree}
    families={allFamilies}
    onsuccess={onCreateCanisterSuccess}
    oncancel={() => (createCanisterStand = null)}
  />
{/if}
