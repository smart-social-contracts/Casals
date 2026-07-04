import { IDL } from '@dfinity/candid';

// Casals conductor — JSON-in / JSON-out interface. Every method takes either no
// args or a single `text` argument (a JSON string) and returns a `text` (a JSON
// string). The candid surface is intentionally tiny; the real shapes live in the
// JSON payloads handled by `api.ts`.
export const idlFactory = ({ IDL }: { IDL: any }) => {
  return IDL.Service({
    // ── queries ──
    get_status:            IDL.Func([], [IDL.Text], ['query']),
    casals_metadata:       IDL.Func([], [IDL.Text], ['query']),
    get_settings:          IDL.Func([], [IDL.Text], ['query']),
    get_tree:              IDL.Func([], [IDL.Text], ['query']),
    list_sections:         IDL.Func([], [IDL.Text], ['query']),
    list_authorized_wasms: IDL.Func([IDL.Text], [IDL.Text], ['query']),
    get_events:            IDL.Func([IDL.Text], [IDL.Text], ['query']),
    get_canister_deployment: IDL.Func([IDL.Text], [IDL.Text], ['query']),
    get_sheet:             IDL.Func([], [IDL.Text], ['query']),
    list_pool:             IDL.Func([], [IDL.Text], ['query']),
    pool_remove:           IDL.Func([IDL.Text], [IDL.Text], []),
    list_arrangements:     IDL.Func([], [IDL.Text], ['query']),
    get_arrangement:       IDL.Func([IDL.Text], [IDL.Text], ['query']),
    set_arrangement:       IDL.Func([IDL.Text], [IDL.Text], []),
    set_active_arrangement: IDL.Func([IDL.Text], [IDL.Text], []),
    delete_arrangement:    IDL.Func([IDL.Text], [IDL.Text], []),
    apply_arrangement:     IDL.Func([IDL.Text], [IDL.Text], []),

    // ── cycles management ──
    get_cycles:            IDL.Func([], [IDL.Text], []),
    get_cycles_cached:     IDL.Func([], [IDL.Text], ['query']),
    refresh_canisters:     IDL.Func([IDL.Text], [IDL.Text], []),
    refresh_treasury:      IDL.Func([IDL.Text], [IDL.Text], []),
    get_cycle_history:     IDL.Func([IDL.Text], [IDL.Text], ['query']),
    get_treasury_flow:     IDL.Func([IDL.Text], [IDL.Text], ['query']),
    top_up:                IDL.Func([IDL.Text], [IDL.Text], []),
    return_cycles:         IDL.Func([IDL.Text], [IDL.Text], []),
    convert_treasury_icp:  IDL.Func([], [IDL.Text], []),
    reconcile:             IDL.Func([], [IDL.Text], []),
    set_cycle_policy:      IDL.Func([IDL.Text], [IDL.Text], []),

    // ── sheet (persistent desired-orchestra) ──
    set_sheet:             IDL.Func([IDL.Text], [IDL.Text], []),
    reset_sheet:           IDL.Func([], [IDL.Text], []),
    deploy_sheet:          IDL.Func([IDL.Text], [IDL.Text], []),

    // ── governance / registration ──
    set_settings:          IDL.Func([IDL.Text], [IDL.Text], []),
    set_subnet_whitelist:    IDL.Func([IDL.Text], [IDL.Text], []),
    create_section:        IDL.Func([IDL.Text], [IDL.Text], []),
    create_stand:           IDL.Func([IDL.Text], [IDL.Text], []),
    set_commander:         IDL.Func([IDL.Text], [IDL.Text], []),
    remove_commander:      IDL.Func([IDL.Text], [IDL.Text], []),
    set_permissions:       IDL.Func([IDL.Text], [IDL.Text], []),
    list_permissions:      IDL.Func([], [IDL.Text], ['query']),
    list_backend_controllers: IDL.Func([IDL.Text], [IDL.Text], []),
    list_orchestration_actions: IDL.Func([], [IDL.Text], ['query']),
    get_orchestration_policies: IDL.Func([IDL.Text], [IDL.Text], ['query']),
    set_orchestration_policies: IDL.Func([IDL.Text], [IDL.Text], []),
    list_governance_requests: IDL.Func([IDL.Text], [IDL.Text], ['query']),
    approve_governance_request: IDL.Func([IDL.Text], [IDL.Text], []),
    reject_governance_request: IDL.Func([IDL.Text], [IDL.Text], []),
    rename_section:        IDL.Func([IDL.Text], [IDL.Text], []),
    rename_stand:           IDL.Func([IDL.Text], [IDL.Text], []),
    rename_canister:          IDL.Func([IDL.Text], [IDL.Text], []),
    delete_section:        IDL.Func([IDL.Text], [IDL.Text], []),
    delete_stand:           IDL.Func([IDL.Text], [IDL.Text], []),
    delete_canister:          IDL.Func([IDL.Text], [IDL.Text], []),
    destroy_canister:         IDL.Func([IDL.Text], [IDL.Text], []),
    register_canister:        IDL.Func([IDL.Text], [IDL.Text], []),
    add_authorized_wasm:   IDL.Func([IDL.Text], [IDL.Text], []),
    remove_authorized_wasm: IDL.Func([IDL.Text], [IDL.Text], []),

    // ── lifecycle (management-canister orchestration) ──
    assign_pool_canister:   IDL.Func([IDL.Text], [IDL.Text], []),
    create_canister:          IDL.Func([IDL.Text], [IDL.Text], []),
    upgrade_to:            IDL.Func([IDL.Text], [IDL.Text], []),
    create_snapshot:       IDL.Func([IDL.Text], [IDL.Text], []),
    revert_snapshot:       IDL.Func([IDL.Text], [IDL.Text], []),
    stop_canister:         IDL.Func([IDL.Text], [IDL.Text], []),
    start_canister:        IDL.Func([IDL.Text], [IDL.Text], []),
    set_log_visibility:    IDL.Func([IDL.Text], [IDL.Text], []),
    canister_browse:          IDL.Func([IDL.Text], [IDL.Text], []),
    canister_exec:            IDL.Func([IDL.Text], [IDL.Text], []),
    orchestration_status:     IDL.Func([IDL.Text], [IDL.Text], ['query']),
    orchestration_refresh:    IDL.Func([IDL.Text], [IDL.Text], []),
    orchestration_hand_to_baton: IDL.Func([IDL.Text], [IDL.Text], []),
    orchestration_configure_baton: IDL.Func([IDL.Text], [IDL.Text], []),
    orchestration_prepare_managed_upgrade: IDL.Func([IDL.Text], [IDL.Text], []),
    orchestration_prepare_asset_provision: IDL.Func([IDL.Text], [IDL.Text], []),
    orchestration_execute_action: IDL.Func([IDL.Text], [IDL.Text], []),
    list_subnets:          IDL.Func([], [IDL.Text], []),
    estimate_deploy:       IDL.Func([IDL.Text], [IDL.Text], ['query']),
    refresh_fx:            IDL.Func([], [IDL.Text], []),
    sync_controllers:      IDL.Func([IDL.Text], [IDL.Text], []),
    refresh_controllers_cache: IDL.Func([IDL.Text], [IDL.Text], []),
  });
};
