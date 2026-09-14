import Array "mo:core/Array";
import Cycles "mo:core/Cycles";
import IC "mo:core/InternetComputer";
import Nat "mo:core/Nat";
import Principal "mo:core/Principal";
import Text "mo:core/Text";
import Time "mo:core/Time";

import JsonParse "json_parse";
import SweepWasm "SweepWasm";
import Types "types";

persistent actor Self {
  type Capability = Types.Capability;
  type BatonAction = Types.BatonAction;
  type ProposalStatus = Types.ProposalStatus;
  type Proposal = Types.Proposal;
  type AuditEvent = Types.AuditEvent;
  type Result = Types.Result;
  type ExecuteResult = Types.ExecuteResult;
  type Timestamp = Types.Timestamp;

  private let VERSION : Text = "1.5.0";
  private let MAX_APPLY_ITERATIONS : Nat = 50;

  private stable var signers : [Principal] = [];
  private stable var threshold : Nat = 1;
  private stable var proposal_expiry_secs : Nat = 604800;
  private stable var next_proposal_id : Nat = 0;
  private stable var proposal_entries : [(Nat, Proposal)] = [];
  private stable var event_log : [AuditEvent] = [];

  private func now() : Timestamp { Time.now() };

  private func log(kind : Text, detail : Text) {
    event_log := Array.concat(event_log, [{ at = now(); kind; detail }]);
  };

  private func isSigner(p : Principal) : Bool {
    Array.find<Principal>(signers, func(x) { x == p }) != null;
  };

  private func validateSigners(th : Nat, ss : [Principal]) : Result {
    let m = ss.size();
    if (m == 0) { return #err("signer set cannot be empty") };
    if (th == 0 or th > m) { return #err("threshold must satisfy 1 <= n <= m") };
    #ok;
  };

  public shared ({ caller }) func configure(
    init_signers : [Principal],
    init_threshold : Nat,
    expiry_secs : Nat,
  ) : async Result {
    if (signers.size() != 0) { return #err("already configured") };
    switch (validateSigners(init_threshold, init_signers)) {
      case (#ok) {};
      case (#err(e)) { return #err(e) };
    };
    signers := init_signers;
    threshold := init_threshold;
    proposal_expiry_secs := expiry_secs;
    log("configured", Nat.toText(init_threshold));
    #ok;
  };

  private func setProposal(id : Nat, p : Proposal) {
    var out : [(Nat, Proposal)] = [];
    var found = false;
    for ((k, v) in proposal_entries.vals()) {
      if (k == id) { out := Array.concat(out, [(k, p)]); found := true }
      else { out := Array.concat(out, [(k, v)]) };
    };
    if (not found) { out := Array.concat(out, [(id, p)]) };
    proposal_entries := out;
  };

  private func getProposal(id : Nat) : ?Proposal {
    for ((k, v) in proposal_entries.vals()) {
      if (k == id) return ?v;
    };
    null;
  };

  private func hasApproved(p : Proposal, signer : Principal) : Bool {
    Array.find<Principal>(p.approvals, func(x) { x == signer }) != null;
  };

  private func tryExecute(p : Proposal) : async Proposal {
    if (p.approvals.size() < threshold) return p;
    switch (p.status) {
      case (#pending) {};
      case (_) { return p };
    };
    let executed = await executeAction(p.action);
    switch (executed) {
      case (#ok(r)) {
        log("executed", "proposal " # Nat.toText(p.id));
        { p with status = #executed; result = r };
      };
      case (#err(e)) {
        log("execute_failed", e);
        { p with status = #failed; result = ?e };
      };
    };
  };

  /// Casals destroy_* returns JSON ``{"ok": true, ...}`` or ``{"ok": false, "error": "..."}``.
  private func casalsResponseOk(resp : Text) : Bool {
    Text.contains(resp, #text "\"ok\": true") or Text.contains(resp, #text "\"ok\":true");
  };

  /// Escalating headroom left on a doomed canister while it deposits to
  /// the treasury. Same ladder as Casals ``DESTROY_SWEEP_RESERVES``.
  private let SWEEP_RESERVES : [Nat] = [
    8_000_000_000,
    16_000_000_000,
    32_000_000_000,
    64_000_000_000,
    128_000_000_000,
    256_000_000_000,
  ];

  /// Reinstall the tiny sweeper on ``cid`` (multisig is the controller) and
  /// deposit almost all of its cycles to the Casals treasury *before* delete.
  /// IC ``delete_canister`` burns leftovers — it does not credit the caller.
  /// Casals is never added as a controller.
  private func drainToTreasury(cid : Principal, treasury : Principal) : async Result {
    if (Principal.isAnonymous(treasury) or treasury == Principal.fromActor(Self)) {
      return #err("invalid treasury");
    };
    let ic00 = actor ("aaaaa-aa") : actor {
      update_settings : shared {
        canister_id : Principal;
        settings : {
          controllers : ?[Principal];
          compute_allocation : ?Nat;
          memory_allocation : ?Nat;
          freezing_threshold : ?Nat;
        };
      } -> async ();
      install_code : shared {
        mode : { #install; #reinstall; #upgrade };
        canister_id : Principal;
        wasm_module : Blob;
        arg : Blob;
      } -> async ();
      start_canister : shared { canister_id : Principal } -> async ();
      canister_status : shared { canister_id : Principal } -> async {
        cycles : Nat;
        status : { #running; #stopping; #stopped };
        memory_size : Nat;
        settings : {
          controllers : [Principal];
          compute_allocation : Nat;
          memory_allocation : Nat;
          freezing_threshold : Nat;
        };
        idle_cycles_burned_per_day : Nat;
        module_hash : ?Blob;
        reserved_cycles : Nat;
      };
    };
    try {
      await ic00.update_settings({
        canister_id = cid;
        settings = {
          controllers = null;
          compute_allocation = null;
          memory_allocation = null;
          freezing_threshold = ?0;
        };
      });
    } catch (_) {
      return #err("update_settings failed: " # Principal.toText(cid));
    };
    try {
      await ic00.install_code({
        mode = #reinstall;
        canister_id = cid;
        wasm_module = SweepWasm.wasm;
        arg = "";
      });
    } catch (_) {
      return #err("install sweeper failed: " # Principal.toText(cid));
    };
    try { await ic00.start_canister({ canister_id = cid }) } catch (_) {};

    let sweeper = actor (Principal.toText(cid)) : actor {
      sweep : shared (Principal, Nat) -> async ();
    };
    var lastErr : Text = "sweep failed";
    for (reserve in SWEEP_RESERVES.vals()) {
      let st = try {
        await ic00.canister_status({ canister_id = cid });
      } catch (_) {
        return #err("canister_status failed: " # Principal.toText(cid));
      };
      if (st.cycles <= reserve) {
        return #ok;
      };
      let amount = st.cycles - reserve;
      try {
        await sweeper.sweep(treasury, amount);
        log("cycles_swept", Principal.toText(cid) # " " # Nat.toText(amount));
        return #ok;
      } catch (_) {
        lastErr := "sweep failed at reserve " # Nat.toText(reserve);
      };
    };
    #err(lastErr # ": " # Principal.toText(cid));
  };

  /// Drain to ``treasury`` first, then stop + delete as this actor.
  /// Do not send after delete — leftovers are already burned.
  /// Casals is never a controller; only the multisig may call management.
  private func destroyCanistersOnIc(ids : [Principal], treasury : Principal) : async Result {
    let ic00 = actor ("aaaaa-aa") : actor {
      stop_canister : shared { canister_id : Principal } -> async ();
      delete_canister : shared { canister_id : Principal } -> async ();
    };
    for (cid in ids.vals()) {
      try {
        await ic00.stop_canister({ canister_id = cid });
      } catch (_) {
        // already stopped or not running
      };
      switch (await drainToTreasury(cid, treasury)) {
        case (#err(e)) { return #err(e) };
        case (#ok) {};
      };
      try {
        await ic00.stop_canister({ canister_id = cid });
      } catch (_) {};
      try {
        await ic00.delete_canister({ canister_id = cid });
      } catch (_) {
        return #err("delete_canister failed: " # Principal.toText(cid));
      };
    };
    #ok;
  };

  private func casalsErrorDetail(resp : Text) : Text {
    // Prefer the JSON body when present; fall back to a short label.
    if (Text.size(resp) == 0) { "casals returned empty response" } else { resp };
  };

  private func encodeCaps(caps : [Capability]) : Text {
    var first = true;
    var out = "[";
    for (c in caps.vals()) {
      if (not first) { out := out # "," };
      out := out # "\"" # c # "\"";
      first := false;
    };
    out # "]";
  };

  private func applySheetPayload(
    plan_hash : Text,
    max_items : Nat,
    confirm_destructive : Bool,
  ) : Text {
    "{\"plan_hash\":\"" # plan_hash # "\",\"max_items\":" # Nat.toText(max_items) #
      ",\"confirm_destructive\":" # (if confirm_destructive { "true" } else { "false" }) # "}";
  };

  private func applySheetSummary(
    iterations : Nat,
    total_applied : Nat,
    remaining : Nat,
    next_plan_hash : ?Text,
    note : Text,
  ) : Text {
    var out =
      "iterations=" # Nat.toText(iterations) #
      " applied=" # Nat.toText(total_applied) #
      " remaining=" # Nat.toText(remaining);
    switch (next_plan_hash) {
      case (?h) { out := out # " next_plan_hash=" # h };
      case null {};
    };
    if (note.size() > 0) { out # " " # note } else { out };
  };

  private func executeApplySheet(a : {
    casals_backend : Principal;
    plan_hash : Text;
    confirm_destructive : Bool;
    max_items : Nat;
  }) : async ExecuteResult {
    let casals = actor (Principal.toText(a.casals_backend)) : actor {
      apply : shared Text -> async Text;
    };
    var plan_hash = a.plan_hash;
    var iterations : Nat = 0;
    var total_applied : Nat = 0;
    var last_remaining : Nat = 0;
    var last_next : ?Text = null;
    label apply_loop while (iterations < MAX_APPLY_ITERATIONS) {
      iterations += 1;
      let payload = applySheetPayload(plan_hash, a.max_items, a.confirm_destructive);
      let resp = try {
        await casals.apply(payload);
      } catch (_) {
        return #err("apply call failed");
      };
      if (JsonParse.responseNotOk(resp)) {
        let summary = applySheetSummary(
          iterations,
          total_applied,
          last_remaining,
          last_next,
          "error=" # JsonParse.truncate(resp, 512),
        );
        return #err(summary);
      };
      total_applied += JsonParse.countAppliedOk(resp);
      last_remaining := switch (JsonParse.remaining(resp)) {
        case (?r) r;
        case null {
          return #err(
            applySheetSummary(iterations, total_applied, 0, last_next, "error=missing remaining"),
          );
        };
      };
      last_next := JsonParse.nextPlanHash(resp);
      if (JsonParse.failedNonNull(resp)) {
        let summary = applySheetSummary(
          iterations,
          total_applied,
          last_remaining,
          last_next,
          "failed=" # JsonParse.truncate(resp, 512),
        );
        return #ok(?summary);
      };
      if (last_remaining == 0) {
        return #ok(
          ?applySheetSummary(iterations, total_applied, 0, last_next, ""),
        );
      };
      switch (last_next) {
        case (?h) { plan_hash := h };
        case null {};
      };
    };
    #err(
      applySheetSummary(
        iterations,
        total_applied,
        last_remaining,
        last_next,
        "error=iteration cap " # Nat.toText(MAX_APPLY_ITERATIONS),
      ),
    );
  };

  private func executeCallCanister(a : {
    canister : Principal;
    method : Text;
    arg_json : Text;
  }) : async ExecuteResult {
    let raw = try {
      await IC.call(a.canister, a.method, to_candid (a.arg_json));
    } catch (_) {
      return #err("call failed: " # Principal.toText(a.canister) # "." # a.method);
    };
    let resp : ?Text = from_candid (raw);
    switch (resp) {
      case null { #err("empty reply") };
      case (?text) { #ok(?JsonParse.truncate(text, JsonParse.MAX_RESULT_CHARS)) };
    };
  };

  private func executeAction(action : BatonAction) : async ExecuteResult {
    switch (action) {
      case (#UpgradeBaton(a)) {
        let ic = actor ("aaaaa-aa") : actor {
          install_code : shared {
            mode : { #install; #reinstall; #upgrade };
            canister_id : Principal;
            wasm_module : Blob;
            arg : Blob;
          } -> async ();
        };
        try {
          await ic.install_code({
            mode = #upgrade;
            canister_id = a.baton_id;
            wasm_module = a.wasm_module;
            arg = a.arg;
          });
          #ok(null);
        } catch (_) { #err("install_code failed") };
      };
      case (#UpdateBatonSettings(a)) {
        let ic = actor ("aaaaa-aa") : actor {
          update_settings : shared {
            canister_id : Principal;
            settings : {
              controllers : ?[Principal];
              compute_allocation : ?Nat;
              memory_allocation : ?Nat;
              freezing_threshold : ?Nat;
            };
          } -> async ();
        };
        try {
          await ic.update_settings({
            canister_id = a.baton_id;
            settings = {
              controllers = if (a.add_controllers.size() + a.remove_controllers.size() == 0) null else ?a.add_controllers;
              compute_allocation = null;
              memory_allocation = null;
              freezing_threshold = null;
            };
          });
          #ok(null);
        } catch (_) { #err("update_settings failed") };
      };
      case (#SetCanisterControllers(a)) {
        let ic = actor ("aaaaa-aa") : actor {
          update_settings : shared {
            canister_id : Principal;
            settings : {
              controllers : ?[Principal];
              compute_allocation : ?Nat;
              memory_allocation : ?Nat;
              freezing_threshold : ?Nat;
            };
          } -> async ();
        };
        try {
          await ic.update_settings({
            canister_id = a.canister_id;
            settings = {
              controllers = ?a.controllers;
              compute_allocation = null;
              memory_allocation = null;
              freezing_threshold = null;
            };
          });
          #ok(null);
        } catch (_) { #err("update_settings failed") };
      };
      case (#AddCommander(a)) {
        let baton = actor (Principal.toText(a.baton_id)) : actor {
          add_commander : shared Text -> async Text;
        };
        let payload = "{\"principal\":\"" # Principal.toText(a.commander) # "\",\"capabilities\":" #
          encodeCaps(a.capabilities) # "}";
        try {
          ignore await baton.add_commander(payload);
          #ok(null);
        } catch (_) { #err("add_commander failed") };
      };
      case (#RemoveCommander(a)) {
        let baton = actor (Principal.toText(a.baton_id)) : actor {
          remove_commander : shared Text -> async Text;
        };
        try {
          ignore await baton.remove_commander(Principal.toText(a.commander));
          #ok(null);
        } catch (_) { #err("remove_commander failed") };
      };
      case (#SetPolicy(p)) {
        let baton = actor (Principal.toText(p.baton_id)) : actor {
          set_commander_policy : shared Text -> async Text;
        };
        try {
          ignore await baton.set_commander_policy(p.policy_json);
          log("set_policy", p.policy_json);
          #ok(null);
        } catch (_) { #err("set_commander_policy failed") };
      };
      case (#ManageSigners(a)) {
        var ss = signers;
        for (p in a.add.vals()) {
          if (Array.find<Principal>(ss, func(x) { x == p }) == null) {
            ss := Array.concat(ss, [p]);
          };
        };
        var filtered : [Principal] = [];
        for (p in ss.vals()) {
          var removed = false;
          for (r in a.remove.vals()) { if (r == p) { removed := true } };
          if (not removed) { filtered := Array.concat(filtered, [p]) };
        };
        let th = switch (a.new_threshold) { case (?t) t; case null threshold };
        switch (validateSigners(th, filtered)) {
          case (#ok) { signers := filtered; threshold := th; #ok(null) };
          case (#err(e)) { #err(e) };
        };
      };
      case (#DestroyStand(a)) {
        // Casals.destroy_stand drains to the treasury before any IC delete.
        // This action does not call delete_canister itself.
        let casals = actor (Principal.toText(a.casals_backend)) : actor {
          destroy_stand : shared Text -> async Text;
        };
        let payload = "{\"stand\":\"" # a.stand # "\"}";
        try {
          let resp = await casals.destroy_stand(payload);
          if (casalsResponseOk(resp)) { #ok(null) } else { #err(casalsErrorDetail(resp)) };
        } catch (_) { #err("destroy_stand failed") };
      };
      case (#DestroyCanister(a)) {
        switch (await destroyCanistersOnIc([a.canister_id], a.casals_backend)) {
          case (#ok) { #ok(null) };
          case (#err(e)) { #err(e) };
        };
      };
      case (#DestroyCanisters(a)) {
        switch (await destroyCanistersOnIc(a.canister_ids, a.casals_backend)) {
          case (#ok) { #ok(null) };
          case (#err(e)) { #err(e) };
        };
      };
      case (#ApplySheet(a)) {
        await executeApplySheet(a);
      };
      case (#CallCanister(a)) {
        await executeCallCanister(a);
      };
    };
  };

  public query func default_proposal_expiry_secs() : async Nat {
    proposal_expiry_secs;
  };

  public shared ({ caller }) func propose(action : BatonAction, expiry_secs : ?Nat) : async Nat {
    assert (isSigner(caller));
    let id = next_proposal_id;
    next_proposal_id += 1;
    let secs = switch (expiry_secs) {
      case (?s) {
        assert (s > 0);
        s;
      };
      case null { proposal_expiry_secs };
    };
    let p : Proposal = {
      id;
      action;
      proposed_by = caller;
      approvals = [caller];
      status = #pending;
      created_at = now();
      expires_at = now() + secs * 1_000_000_000;
      result = null;
    };
    log("proposed", Nat.toText(id));
    let executed = await tryExecute(p);
    setProposal(id, executed);
    id;
  };

  public shared ({ caller }) func approve(proposal_id : Nat) : async Result {
    assert (isSigner(caller));
    switch (getProposal(proposal_id)) {
      case null { #err("unknown proposal") };
      case (?p) {
        if (p.status != #pending) return #err("proposal not pending");
        if (now() > p.expires_at) {
          setProposal(proposal_id, { p with status = #expired });
          return #err("proposal expired");
        };
        if (hasApproved(p, caller)) return #err("already approved");
        let updated = {
          p with approvals = Array.concat(p.approvals, [caller]);
        };
        log("approved", Nat.toText(proposal_id));
        let executed = await tryExecute(updated);
        setProposal(proposal_id, executed);
        #ok;
      };
    };
  };

  public shared ({ caller }) func reject(proposal_id : Nat) : async Result {
    assert (isSigner(caller));
    switch (getProposal(proposal_id)) {
      case null { #err("unknown proposal") };
      case (?p) {
        if (p.status != #pending) return #err("proposal not pending");
        setProposal(proposal_id, { p with status = #rejected });
        log("rejected", Nat.toText(proposal_id));
        #ok;
      };
    };
  };

  public query func get_proposal(proposal_id : Nat) : async ?Proposal {
    getProposal(proposal_id);
  };

  public query func list_proposals() : async [Proposal] {
    var out : [Proposal] = [];
    for ((_, p) in proposal_entries.vals()) {
      out := Array.concat(out, [p]);
    };
    out;
  };

  public query func list_signers() : async { signers : [Principal]; threshold : Nat } {
    { signers; threshold };
  };

  public query func list_events() : async [AuditEvent] {
    event_log;
  };

  /// Public cycle balance — used by the create/destroy lock to prove
  /// reclaimed cycles did not stay on this canister after DestroyCanisters.
  public query func cycles_balance() : async Nat {
    Cycles.balance();
  };

  public query func version() : async Text {
    VERSION;
  };
};
