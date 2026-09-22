//! Hello-world canister — Rust.
//!
//! The smallest useful canister, plus the one thing a stand's backend must be
//! able to do in a Casals orchestra: **vote on its own Baton**. The stand's
//! Baton lists this canister as a commander (`$stand.backend` in the sheet), so
//! an upgrade the team proposes only runs once this canister approves it —
//! "the team advises, the user decides". `actions`, `approve` and `reject` are
//! that vote, driven from the stand's frontend. There is no `propose`: proposing
//! is the team's side (`casals upgrade --wasm`).
//!
//! Config (the Baton's id, set by the sheet's `config` item) lives in stable
//! memory so it survives the upgrades it is there to approve.

use candid::Principal;
use ic_cdk::api::call::{call, CallResult};
use ic_stable_structures::memory_manager::{MemoryId, MemoryManager, VirtualMemory};
use ic_stable_structures::{DefaultMemoryImpl, StableCell};
use std::cell::RefCell;

const VERSION: &str = "1.1.0";

type Memory = VirtualMemory<DefaultMemoryImpl>;

thread_local! {
    static MEMORY_MANAGER: RefCell<MemoryManager<DefaultMemoryImpl>> =
        RefCell::new(MemoryManager::init(DefaultMemoryImpl::default()));
    // Config as a JSON object string, in stable memory: an upgrade must not
    // forget where the Baton is.
    static CONFIG: RefCell<StableCell<String, Memory>> = RefCell::new(
        StableCell::init(
            MEMORY_MANAGER.with(|m| m.borrow().get(MemoryId::new(0))),
            String::from("{}"),
        )
        .expect("stable config cell"),
    );
}

#[ic_cdk::query]
fn health_check() -> String {
    r#"{"status":"ok"}"#.to_string()
}

// `greet` is an update (not a query) so its log line is recorded in the canister
// log — the IC does not log non-replicated query calls.
#[ic_cdk::update]
fn greet(name: String) -> String {
    ic_cdk::println!("greet called with name={}", name);
    format!("Hello, {}! (v{})", name, VERSION)
}

// ── Config ────────────────────────────────────────────────────────────────────
// The same text-JSON protocol product canisters use, so a sheet's `config`
// items converge against it:
//   {"method": "set_canister_config_json", "args": {"baton": "$stand.baton"},
//    "converged_when": {"query": "get_canister_config_json", "equals_args": true}}

fn config() -> serde_json::Map<String, serde_json::Value> {
    let raw = CONFIG.with(|c| c.borrow().get().clone());
    match serde_json::from_str::<serde_json::Value>(&raw) {
        Ok(serde_json::Value::Object(m)) => m,
        _ => serde_json::Map::new(),
    }
}

#[ic_cdk::update]
fn set_canister_config_json(args: String) -> String {
    let mut cfg = config();
    if let Ok(serde_json::Value::Object(patch)) = serde_json::from_str::<serde_json::Value>(&args) {
        for (k, v) in patch {
            cfg.insert(k, v);
        }
    }
    let text = serde_json::Value::Object(cfg).to_string();
    CONFIG.with(|c| c.borrow_mut().set(text).expect("stable config cell"));
    r#"{"success":true}"#.to_string()
}

#[ic_cdk::query]
fn get_canister_config_json() -> String {
    serde_json::Value::Object(config()).to_string()
}

// ── Baton client ──────────────────────────────────────────────────────────────
// The Baton speaks text-JSON. This canister is one of its commanders, so calls
// made *from here* carry the vote; the frontend talks to this canister, never
// to the Baton, for anything that changes state.

fn err(message: impl AsRef<str>) -> String {
    serde_json::json!({ "ok": false, "error": message.as_ref() }).to_string()
}

fn baton() -> Result<Principal, String> {
    let cfg = config();
    let id = cfg.get("baton").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    if id.is_empty() {
        return Err("no baton configured: set_canister_config_json {\"baton\": <canister id>}".into());
    }
    Principal::from_text(&id).map_err(|e| format!("bad baton id {id}: {e}"))
}

/// The Baton's own JSON reply, or an error envelope when the call failed.
fn reply(res: CallResult<(String,)>) -> String {
    match res {
        Ok((text,)) => text,
        Err((code, msg)) => err(format!("baton call failed: {code:?} {msg}")),
    }
}

/// The Baton's action list (`list_actions`), as the Baton returns it.
/// An update, not a query: a query cannot make inter-canister calls.
#[ic_cdk::update]
async fn actions() -> String {
    match baton() {
        Ok(b) => reply(call::<(), (String,)>(b, "list_actions", ()).await),
        Err(e) => err(e),
    }
}

/// Cast this stand's vote for a pending Baton action (`submit_approval`).
/// Once the Baton's quorum is met it runs the pipeline on its own.
#[ic_cdk::update]
async fn approve(action_id: String) -> String {
    ic_cdk::println!("approve action {}", action_id);
    match baton() {
        Ok(b) => reply(call::<(String,), (String,)>(b, "submit_approval", (action_id.trim().to_string(),)).await),
        Err(e) => err(e),
    }
}

/// Reject a pending Baton action (`reject_action`).
#[ic_cdk::update]
async fn reject(action_id: String) -> String {
    ic_cdk::println!("reject action {}", action_id);
    match baton() {
        Ok(b) => reply(call::<(String,), (String,)>(b, "reject_action", (action_id.trim().to_string(),)).await),
        Err(e) => err(e),
    }
}

ic_cdk::export_candid!();
