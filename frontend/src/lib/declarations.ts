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
    cycleops_monitored:    IDL.Func([], [IDL.Text], ['query']),

    // ── governance / registration ──
    set_settings:          IDL.Func([IDL.Text], [IDL.Text], []),
    create_section:        IDL.Func([IDL.Text], [IDL.Text], []),
    create_desk:           IDL.Func([IDL.Text], [IDL.Text], []),
    set_commander:         IDL.Func([IDL.Text], [IDL.Text], []),
    register_stand:        IDL.Func([IDL.Text], [IDL.Text], []),
    add_authorized_wasm:   IDL.Func([IDL.Text], [IDL.Text], []),
    remove_authorized_wasm: IDL.Func([IDL.Text], [IDL.Text], []),

    // ── lifecycle (management-canister orchestration) ──
    create_stand:          IDL.Func([IDL.Text], [IDL.Text], []),
    upgrade_to:            IDL.Func([IDL.Text], [IDL.Text], []),
    create_snapshot:       IDL.Func([IDL.Text], [IDL.Text], []),
    revert_snapshot:       IDL.Func([IDL.Text], [IDL.Text], []),
    stop_canister:         IDL.Func([IDL.Text], [IDL.Text], []),
    start_canister:        IDL.Func([IDL.Text], [IDL.Text], []),
  });
};
